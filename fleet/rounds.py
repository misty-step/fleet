"""Review rounds per pull request, and the loop rule.

A round is a review round: 1 is the first review, and the fix round is round - 1.
Rounds come from child thread titles and IDs that name a PR ("harness#447 round 2",
"…-pr447-r2") and, when `gh` is available, from the ship-loop PR comment ledger,
whose round wins. The source reads only ledgers; titles are read fresh in `apply`.
Partial shape: {"owner/repo#number": {"round": int, "source": "ledger", "since": str|None}}.
"""
import json
import re
import shutil
import subprocess
import time

MARKER = "<!-- ship-loop:event v1 -->"
EVENT = re.compile(r"^<!-- ship-loop:event v1 -->\n```json\n([\s\S]*)\n```\s*$")
LEDGER_SECONDS = 60
AUTH_SECONDS = 600

QUALIFIED = re.compile(r"(?<![\w.-])(?:[\w.-]+/)?([A-Za-z0-9][\w.-]*)#(\d+)(?!\d)")
BARE = re.compile(r"(?<![\w#])#(\d+)(?!\d)|(?<![a-z0-9])pr[-#]?(\d+)(?!\d)", re.IGNORECASE)
MARKERS = re.compile(r"(?<![a-z0-9])(?:(?:round|fix|recheck)[\s_-]*(\d{1,2})|r(\d))(?![0-9a-z])", re.IGNORECASE)   # "r" takes one digit: "R75" is a name
REVIEW_ROLE = re.compile(r"(?<![a-z0-9])(?:qa|review\w*|judge)(?![a-z0-9])", re.IGNORECASE)


def key(pr):
    return "%s#%s" % (pr["repository"], pr["number"])


class Gh:
    """`gh api` reads; `available` is false without gh or a login, checked at most every ten minutes."""

    def __init__(self, binary="gh", clock=time.monotonic):
        self.binary = binary
        self.clock = clock
        self.checked = None
        self.ok = False

    def available(self):
        if self.checked is None or self.clock() - self.checked >= AUTH_SECONDS:
            self.checked = self.clock()
            path = shutil.which(self.binary)
            self.ok = bool(path) and run([path, "auth", "status"]) is not None
        return self.ok

    def api(self, *args):
        out = run([self.binary, "api", *args])
        if out is None:
            raise LedgerUnreadable()
        return out


def run(command):
    try:
        done = subprocess.run(command, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


class LedgerUnreadable(Exception):
    pass


def ledger(gh, repository, number):
    """{"round", "since"} from the PR's ship-loop ledger, or None when it has none."""
    author = gh.api("repos/%s/pulls/%d" % (repository, number), "--jq", ".user.login // empty").strip()
    if not author:
        raise LedgerUnreadable()
    lines = gh.api("--paginate", "repos/%s/issues/%d/comments" % (repository, number),
                   "--jq", ".[] | [.id, .body, .user.login, .created_at] | @json")
    comments = [json.loads(line) for line in lines.splitlines() if line.strip()]
    return replay(comments, author)


def replay(comments, author):
    """Reduce the PR author's event comments to a round. Only the PR author writes the ledger."""
    events = {}
    for comment_id, body, login, created in sorted(comments, key=lambda c: c[0]):
        if (login or "").lower() != author.lower() or not (body or "").startswith(MARKER):
            continue
        match = EVENT.match(body)
        if not match:
            raise LedgerUnreadable()
        try:
            event = json.loads(match.group(1))
        except ValueError:
            raise LedgerUnreadable() from None
        if not isinstance(event, dict) or not isinstance(event.get("seq"), int) or not isinstance(event.get("kind"), str):
            raise LedgerUnreadable()
        events.setdefault(event["seq"], (event, created))   # a lost concurrent write: the lowest id wins
    if not events:
        return None
    if sorted(events) != list(range(1, len(events) + 1)) or events[1][0]["kind"] != "init":
        raise LedgerUnreadable()
    round_, since = 1, events[1][1]
    for seq in sorted(events):
        event, created = events[seq]
        if event["kind"] == "verdict" and event.get("verdict") == "FIX":
            round_, since = round_ + 1, created
    return {"round": round_, "since": since}


class Rounds:
    name = "rounds"
    interval = 30

    def __init__(self, config, clients, gh=None, clock=time.monotonic):
        self.clients = clients
        self.gh = gh or Gh()
        self.clock = clock
        self.cache = {}   # PR key -> {"at": monotonic, "result": {...} | None}

    def collect(self, hub):
        t3 = hub.partial("t3") or {}
        if self.gh.available():
            for pr in open_prs(t3.get("threads", [])):
                entry = self.cache.get(key(pr))
                if entry and self.clock() - entry["at"] < LEDGER_SECONDS:
                    continue
                previous = entry["result"] if entry else None
                try:
                    result = ledger(self.gh, pr["repository"], int(pr["number"]))
                except (LedgerUnreadable, ValueError, TypeError):
                    result = previous   # unreadable now: keep what was known, try again in a minute
                self.cache[key(pr)] = {"at": self.clock(), "result": result}
        return {name: {"round": entry["result"]["round"], "source": "ledger", "since": entry["result"]["since"]}
                for name, entry in self.cache.items() if entry["result"]}


def open_prs(threads):
    seen = {}
    for thread in threads:
        for pr in thread.get("prs") or []:
            if pr.get("state") == "open":
                seen.setdefault(key(pr), pr)
    return list(seen.values())


def from_threads(threads):
    """PR key -> {"round", "since"} from child thread titles and IDs that name the PR."""
    by_id = {(t["server"], t["threadId"]): t for t in threads}

    def root(thread):
        hops = 0
        while thread.get("parentThreadId") and (thread["server"], thread["parentThreadId"]) in by_id and hops < 50:
            thread, hops = by_id[(thread["server"], thread["parentThreadId"])], hops + 1
        return thread["server"], thread["threadId"]

    roots = {(t["server"], t["threadId"]): root(t) for t in threads}
    tree_prs, all_prs = {}, {}
    for thread in threads:
        for pr in thread.get("prs") or []:
            tree_prs.setdefault(roots[(thread["server"], thread["threadId"])], {})[key(pr)] = pr
            all_prs[key(pr)] = pr

    found = {}   # PR key -> [(round, createdAt)]
    for thread in threads:
        if not thread.get("parentThreadId"):
            continue
        text = "%s %s" % (thread.get("title") or "", thread.get("threadId") or "")
        names = named(text, tree_prs.get(roots[(thread["server"], thread["threadId"])], {}), all_prs)
        if not names:
            continue
        marks = [int(word or short) for word, short in MARKERS.findall(text) if int(word or short) > 0]
        round_ = max(marks) if marks else (1 if REVIEW_ROLE.search(thread.get("title") or "") else None)
        if round_ is None:
            continue
        for name in names:
            found.setdefault(name, []).append((round_, thread.get("createdAt") or ""))

    known = {}
    for name, hits in found.items():
        top = max(r for r, _ in hits)
        times = sorted(at for r, at in hits if r == top and at)
        known[name] = {"round": top, "since": times[0] if times else None}
    return known


def named(text, tree, everywhere):
    """PR keys a title or ID names: "repo#N" anywhere in the fleet, "#N" or "prN" within its lane."""
    names = set()
    for repo, number in QUALIFIED.findall(text):
        names.update(name for name, pr in everywhere.items()
                     if str(pr["number"]) == number and pr["repository"].lower().rsplit("/", 1)[-1] == repo.lower())
    for hash_number, pr_number in BARE.findall(text):
        number = hash_number or pr_number
        names.update(name for name, pr in tree.items() if str(pr["number"]) == number)
    return names


def known(context):
    """Rounds from titles, overridden by the ledger where one was read."""
    rounds = from_threads(context.threads)
    for name, entry in (context.rounds or {}).items():
        titled = rounds.get(name)
        since = titled["since"] if titled and titled["round"] == entry["round"] and titled["since"] else entry.get("since")
        rounds[name] = {"round": entry["round"], "since": since}
    return rounds


def apply(lanes, context):
    """Fill each lane PR's round. Merged and closed PRs keep what is known."""
    rounds = known(context)
    for lane in lanes:
        for pr in lane.get("prs") or []:
            entry = rounds.get(key(pr))
            pr["round"] = entry["round"] if entry else pr.get("round")


def attention(lanes, context):
    """Attention items of kind "loop": an open PR at fix round `loopRound` or later."""
    rounds = known(context)
    items = []
    for lane in lanes:
        for pr in lane.get("prs") or []:
            round_ = pr.get("round")
            if pr.get("state") != "open" or round_ is None or round_ - 1 < context.thresholds.loop_round:
                continue
            items.append({
                "kind": "loop", "severity": "bad", "threadId": lane["threadId"], "server": lane["server"],
                "title": lane["title"], "laneTitle": lane["title"],
                "reason": "PR #%s is at fix round %d (review round %d)" % (pr["number"], round_ - 1, round_),
                "since": (rounds.get(key(pr)) or {}).get("since"), "prUrl": pr.get("url"),
            })
    return items
