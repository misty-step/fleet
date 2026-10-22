"""Incremental activity, lane side effects, and the quiet rule.

Commands recognized as deploys: wrangler deploy/publish, vercel (deploy or
--prod), fly/flyctl deploy, and netlify deploy, optionally via npx/bunx.
Native tools: push_files, create_pull_request, merge_pull_request,
add_issue_comment, create_pull_request_review, and deploy.
Only command input is summarized, never command output.
Partial: threadId -> {currentStep, lastSideEffect, runStartedAt, error}. A thread whose
read fails keeps its last good data, and `error` ({"error": short code, "since": first
failure}) stays set until a read succeeds; each lane shows its subtree's as activityError.
"""
import json
import re
import shlex
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from .derive import ACTIVE

PAGE_SIZE = 100
LOOKBACK = 500
TOOL_KINDS = {
    "push_files": "push", "create_pull_request": "pr",
    "merge_pull_request": "merge", "add_issue_comment": "comment",
    "create_pull_request_review": "comment", "deploy": "deploy",
}


def moment(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def summary(text, limit):
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:token|password|secret|api[_-]?key)\s*[=:]\s*)\S+",
                  r"\1[redacted]", text)
    text = re.sub(r"[A-Za-z0-9_+/=-]{24,}", "[redacted]", text)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


HEREDOC = re.compile(r"(?<!<)<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
STATEMENT = re.compile(r"[\w.~/$][\w.+~/=$-]*$|\[\[?$|\]\]?$|[(){}!#]")   # how a statement's first word can start
SETUP = {"set", "export", "cd", "source", "."}
JOINS = {"&&", "||", "|"}


def command_lines(text, script=False):
    """Shell statements from a command's text, one per logical line, input only.

    A timeline command is "$ " and the command, then its output, with no marker
    between them. Statements continue across lines inside quotes, after a trailing
    backslash and through heredoc bodies. A later line is read as a statement only
    after shell setup (set, export, cd, source, comments), a trailing &&, || or |,
    or a heredoc, and only if it can start a statement; otherwise it begins the
    output. So a side effect after any other statement on its own line is missed,
    and output after setup or a heredoc that reads as a command can still be taken
    for input. A `bash -c` script, or a `script` (tool input), has no output, so
    every line is a statement.
    """
    lines = (text or "").removeprefix("$ ").splitlines()
    statements, index, follows = [], 0, True
    while index < len(lines):
        statement, index = lines[index], index + 1
        while True:
            if statement.endswith("\\") and index < len(lines):
                statement, index = statement[:-1] + lines[index], index + 1
                continue
            try:
                words = shlex.split(statement, comments=True)
            except ValueError:   # an open quote continues on the next line
                if index < len(lines):
                    statement, index = statement + "\n" + lines[index], index + 1
                    continue
                words = None
            break
        stripped = statement.strip()
        if not stripped:
            continue
        if statements and not (follows and STATEMENT.match(stripped.split(None, 1)[0])):
            break
        heredocs = HEREDOC.findall(statement)
        for _, delimiter in heredocs:
            if delimiter in (line.strip() for line in statement.splitlines()[1:]):
                continue   # the body was inside the quoted statement
            while index < len(lines) and lines[index].strip() != delimiter:
                index += 1
            index += 1
        if (words and len(words) >= 3 and words[0].rsplit("/", 1)[-1] in {"bash", "sh", "zsh"}
                and words[1] in {"-c", "-lc", "-ic"}):
            statements.extend(command_lines(words[2], script=True))
        else:
            statements.append(stripped)
        parts = tokens(statement)
        follows = script or bool(heredocs) or parts is not None and (
            bool(parts) and parts[-1] in JOINS or all(words[0] in SETUP for words in segments(parts)))
    return statements


def command_text(text, script=False):
    """The command's first statement; its output is never read."""
    statements = command_lines(text, script)
    return statements[0] if statements else ""


def command_effect(text, script=False):
    """(kind, statement) of the command's first side effect, or (None, None)."""
    for statement in command_lines(text, script):
        kind = command_kind(statement)
        if kind:
            return kind, statement
    return None, None


def tokens(command):
    """Shell words and ;&|() operators of a statement, without comments, or None if unparsable."""
    try:
        return list(shlex.shlex(command, posix=True, punctuation_chars=";&|()"))
    except ValueError:
        return None


def segments(parts):
    """The word lists between a statement's operators."""
    result, segment = [], []
    for token in parts + [";"]:
        if token and all(c in ";&|()" for c in token):
            if segment:
                result.append(segment)
            segment = []
        else:
            segment.append(token)
    return result


def command_kind(command):
    parts = tokens(command)
    if parts is None:
        return None
    for words in segments(parts):
        while words and ("=" in words[0] or words[0] in {"env", "sudo", "npx", "bunx"}):
            words = words[1:]
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        args = words[1:]
        if program == "git":
            while args and args[0].startswith("-"):
                args = args[2:] if args[0] in {"-C", "-c", "--git-dir", "--work-tree"} else args[1:]
            if args and args[0] == "push" and "--dry-run" not in args and "-n" not in args:
                return "push"
        if program == "gh":
            while args and args[0] in {"-R", "--repo", "--hostname"}:
                args = args[2:]
            pair = tuple(args[:2])
            kind = {("pr", "create"): "pr", ("pr", "merge"): "merge",
                    ("pr", "comment"): "comment", ("pr", "review"): "comment",
                    ("issue", "comment"): "comment"}.get(pair)
            if kind:
                return kind
        if "--dry-run" not in args and (
            (program == "wrangler" and args[:1] in (["deploy"], ["publish"])) or
            (program in {"fly", "flyctl", "netlify"} and args[:1] == ["deploy"]) or
            (program == "vercel" and (args[:1] == ["deploy"] or "--prod" in args))
        ):
            return "deploy"
    return None


def describe(item):
    """Return the input-only step, side-effect kind and side-effect text of a summarized T3 item."""
    text = item.get("text") or ""
    kind = item.get("type")
    if kind == "command_execution":
        return (command_text(text),) + command_effect(text)
    if kind == "assistant_message":
        return re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)[0], None, None
    if kind not in {"dynamic_tool", "tool_call"}:
        return None, None, None
    try:
        payload = json.loads(text)
    except ValueError:
        # A large input can be truncated, but its leading JSON fields are usable.
        payload = {}
        for key in ("toolName", "cmd", "command", "threadId", "url", "path"):
            match = re.search(r'"' + key + r'"\s*:\s*("(?:\\.|[^"\\])*")', text)
            if match:
                payload[key] = json.loads(match[1])
    if not isinstance(payload, dict):
        return None, None, None
    name = payload.get("toolName") or item.get("title") or "tool"
    inputs = payload.get("input") or payload.get("arguments") or payload
    if not isinstance(inputs, dict):
        inputs = {}
    command = inputs.get("cmd") or inputs.get("command")
    if isinstance(command, str):
        return (command_text(command, True),) + command_effect(command, True)
    target = next((inputs[key] for key in ("threadId", "url", "repository", "path", "file_path")
                   if isinstance(inputs.get(key), str)), "")
    tool = re.split(r"[./]", name)[-1].removeprefix("mcp__github__")
    step = (name + " " + target).strip()
    return step, TOOL_KINDS.get(tool), step


class Activity:
    name = "activity"

    def __init__(self, config, clients):
        self.interval = max(10, config.interval_seconds)
        self.clients = clients
        self.threads = {}

    def collect(self, hub):
        threads = (hub.partial("t3") or {}).get("threads", [])
        live = {(t["server"], t["threadId"]) for t in threads}
        self.threads = {key: state for key, state in self.threads.items() if key in live}
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = {}
        for thread in threads:
            key = thread["server"], thread["threadId"]
            state = self.threads.setdefault(key, {
                "marker": None, "cursor": max(0, thread.get("itemCount", 0) - LOOKBACK),
                "pending": {}, "stepPosition": -1, "runId": None, "error": None,
                "data": {"currentStep": None, "lastSideEffect": None, "runStartedAt": None},
            })
            marker = thread.get("updatedAt"), thread.get("itemCount")
            if state["marker"] != marker:
                state = deepcopy(state)
                try:
                    if state["runId"] != thread.get("latestRunId"):
                        state["data"]["runStartedAt"] = None
                        state["runId"] = thread.get("latestRunId")
                    for item_id in list(state["pending"]):
                        page = self.clients.call(key[0], "t3_thread_read", {
                            "threadId": key[1], "view": "activity", "itemId": item_id,
                            "limit": 1, "maxCharsPerItem": 2000, "runLimit": 1})
                        self._consume(state, page, thread)
                    while True:
                        page = self.clients.call(key[0], "t3_thread_read", {
                            "threadId": key[1], "view": "activity", "afterPosition": state["cursor"],
                            "limit": PAGE_SIZE, "maxCharsPerItem": 2000, "runLimit": 1})
                        self._consume(state, page, thread)
                        cursor = page.get("nextPosition")
                        if cursor is None or cursor <= state["cursor"]:
                            break
                        state["cursor"] = cursor
                        if not page.get("hasMore"):
                            break
                    state["marker"] = marker
                    state["error"] = None
                except Exception as error:   # keep the last good state; retry on the next cycle
                    from .hub import describe as error_code   # hub imports this module
                    state = self.threads[key]
                    since = (state["error"] or {}).get("since") or now
                    state["error"] = {"error": error_code(error), "since": since}
                else:
                    self.threads[key] = state
            result[key[1]] = dict(state["data"], error=state["error"])
        return result

    @staticmethod
    def _consume(state, page, thread):
        runs = page.get("recentRuns", [])
        latest = next((r for r in runs if r.get("runId") == thread.get("latestRunId")), None)
        if latest:
            state["data"]["runStartedAt"] = latest.get("startedAt") or latest.get("requestedAt")
        for item in page.get("items", []):
            if item.get("visibility") == "inherited":
                continue
            position = item["position"]
            step, kind, effect = describe(item)
            if step and position >= state["stepPosition"]:
                state["data"]["currentStep"] = summary(step, 100)
                state["stepPosition"] = position
            if kind and item.get("status") in {"pending", "running", "waiting"}:
                state["pending"][item["itemId"]] = True
            else:
                state["pending"].pop(item["itemId"], None)
            if kind and item.get("status") == "completed" and moment(item.get("updatedAt")):
                previous = state["data"]["lastSideEffect"]
                if previous is None or moment(item["updatedAt"]) >= moment(previous["at"]):
                    state["data"]["lastSideEffect"] = {
                        "kind": kind, "summary": summary(effect, 80), "at": item["updatedAt"]}


def apply(lanes, context):
    """Use the lane's own latest step, its subtree's newest side effect and oldest read failure."""
    children = {}
    for thread in context.threads:
        children.setdefault((thread["server"], thread.get("parentThreadId")), []).append(thread["threadId"])
    for lane in lanes:
        data = context.activity.get(lane["threadId"], {})
        lane["currentStep"] = data.get("currentStep")
        effects, errors, seen, stack = [], [], set(), [lane["threadId"]]
        while stack:
            thread_id = stack.pop()
            if thread_id in seen:
                continue
            seen.add(thread_id)
            effect = context.activity.get(thread_id, {}).get("lastSideEffect")
            if effect and moment(effect.get("at")):
                effects.append(effect)
            error = context.activity.get(thread_id, {}).get("error")
            if error:
                errors.append(error)
            stack.extend(children.get((lane["server"], thread_id), []))
        lane["lastSideEffect"] = max(effects, key=lambda e: moment(e["at"]), default=None)
        lane["activityError"] = dict(min(errors, key=lambda e: e["since"])) if errors else None


def attention(lanes, context):
    result = []
    for lane in lanes:
        if lane["status"] not in ACTIVE:
            continue
        start = moment(context.activity.get(lane["threadId"], {}).get("runStartedAt"))
        if start is None:
            continue
        effect = moment((lane.get("lastSideEffect") or {}).get("at"))
        since = max(start, effect) if effect else start
        elapsed = context.now - since
        if elapsed <= timedelta(hours=context.thresholds.quiet_hours):
            continue
        minutes = int(elapsed.total_seconds() // 60)
        hours, minutes = divmod(minutes, 60)
        duration = f"{hours} h {minutes} min" if hours else f"{minutes} min"
        unread = "; activity could not be read" if lane.get("activityError") else ""
        result.append({"kind": "quiet", "severity": "warn", "threadId": lane["threadId"],
                       "server": lane["server"], "title": lane["title"], "laneTitle": lane["title"],
                       "reason": "Running with no push, PR, merge, deploy or comment for " + duration + unread,
                       "since": since.isoformat().replace("+00:00", "Z"), "prUrl": None})
    return result
