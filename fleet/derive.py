"""Pure rules that turn source data into the snapshot's lanes, attention, tidy and accounts.

Each function takes plain data and returns lists shaped as in docs/snapshot.md, so
every rule is testable without a network. Later PRs fill these in; until then each
returns an empty list and the page shows empty sections.
"""
from dataclasses import dataclass, field
from datetime import datetime

ACTIVE = {"running", "starting", "preparing", "queued"}
DONE = {"completed", "cancelled"}


@dataclass
class Context:
    threads: list            # normalized threads from the t3 source, children included
    roster: set              # harness wipRoots thread IDs
    now: object              # timezone-aware datetime
    thresholds: object       # config.Thresholds
    activity: dict = field(default_factory=dict)   # threadId -> {currentStep, lastSideEffect}
    rounds: dict = field(default_factory=dict)     # "owner/repo#number" -> {round, ...}


def key(thread):
    return thread["server"], thread["threadId"]


class Tree:
    """Parent and child links among one context's threads, per server."""

    def __init__(self, threads):
        self.by_key = {key(thread): thread for thread in threads}
        self.children = {}
        for thread in threads:
            if thread.get("parentThreadId"):
                self.children.setdefault((thread["server"], thread["parentThreadId"]), []).append(thread)

    def descendants(self, thread):
        found, pending, seen = [], list(self.children.get(key(thread), [])), {key(thread)}
        while pending:
            child = pending.pop()
            if key(child) in seen:
                continue
            seen.add(key(child))
            found.append(child)
            pending.extend(self.children.get(key(child), []))
        return found

    def lane(self, thread):
        """The top-level lane that owns a thread, or None when its lane is not unsettled."""
        seen = set()
        while thread.get("parentThreadId") and key(thread) not in seen:
            seen.add(key(thread))
            thread = self.by_key.get((thread["server"], thread["parentThreadId"]))
            if thread is None:
                return None
        return None if thread.get("parentThreadId") else thread


def parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def minutes_since(context, value):
    moment = parse(value)
    return (context.now - moment).total_seconds() / 60 if moment else 0


def duration(minutes):
    hours, rest = divmod(int(minutes), 60)
    return "%d h %d min" % (hours, rest) if hours else "%d min" % rest


def lanes(context):
    tree = Tree(context.threads)
    rows = []
    for thread in context.threads:
        if thread.get("parentThreadId") or thread.get("settled"):
            continue
        family = tree.descendants(thread)
        flags = {found["kind"] for found in (flag(context, member) for member in [thread] + family) if found}
        rows.append({
            "threadId": thread["threadId"],
            "server": thread["server"],
            "project": thread.get("project"),
            "title": thread.get("title"),
            "roster": thread["threadId"] in context.roster,
            "status": thread.get("status"),
            "currentStep": None,
            "lastSideEffect": None,
            "children": {"running": sum(1 for child in family if child.get("status") in ACTIVE | {"waiting"}),
                         "total": len(family)},
            "prs": [dict(pr, round=None) for pr in thread.get("prs", [])],
            "account": thread.get("account"),
            "model": thread.get("model"),
            "updatedAt": thread.get("updatedAt"),
            "flags": sorted(flags),
        })
    return order_lanes(rows)


def order_lanes(rows):
    """Lanes with any attention first, then the most recently updated."""
    rows = sorted(rows, key=lambda row: row.get("updatedAt") or "", reverse=True)
    return sorted(rows, key=lambda row: not row.get("flags"))


def flag(context, thread):
    """The waiting, failed or interrupted attention item for one thread, or None."""
    if thread.get("snoozed") or thread.get("settled"):
        return None
    status, since = thread.get("status"), thread.get("updatedAt")
    if status == "waiting":
        waited = minutes_since(context, since)
        if waited <= context.thresholds.waiting_minutes:
            return None
        questions = thread.get("pendingQuestions")
        on = "a question" if questions else "an approval" if questions == 0 else "an approval or question"
        return item(thread, "waiting", "warn", "Waiting on %s for %s" % (on, duration(waited)), since)
    if status == "failed":
        return item(thread, "failed", "bad", "Latest run failed", since)
    if status == "interrupted":
        return item(thread, "interrupted", "bad", "Latest run interrupted", since)
    return None


def item(thread, kind, severity, reason, since):
    return {"kind": kind, "severity": severity, "threadId": thread["threadId"], "server": thread["server"],
            "title": thread.get("title"), "laneTitle": None, "reason": reason, "since": since, "prUrl": None}


def attention(context, lanes):
    tree = Tree(context.threads)
    items = []
    for thread in context.threads:
        found = flag(context, thread)
        if found:
            lane = tree.lane(thread)
            found["laneTitle"] = lane.get("title") if lane else None
            items.append(found)
    return items


def order(items):
    """Worst first: bad before warn, then the oldest condition first."""
    return sorted(items, key=lambda item: (item["severity"] != "bad", item.get("since") or ""))


def tidy(context):
    tree = Tree(context.threads)
    limits = context.thresholds
    rows = []
    for thread in context.threads:
        status = thread.get("status")
        if thread.get("snoozed") or thread.get("settled") or status in ACTIVE or status == "waiting":
            continue
        idle = minutes_since(context, thread.get("updatedAt"))
        if status in DONE and idle > limits.tidy_minutes:
            reason = "Completed, not settled"
        elif status not in DONE and idle > limits.stale_hours * 60:
            reason = "Idle for over %g h" % limits.stale_hours
        else:
            continue
        lane = tree.lane(thread) if thread.get("parentThreadId") else None
        roster = thread["threadId"] in context.roster
        rows.append({"threadId": thread["threadId"], "server": thread["server"], "title": thread.get("title"),
                     "parentTitle": lane.get("title") if lane else None, "status": status, "reason": reason,
                     "idleSince": thread.get("updatedAt"), "roster": roster,
                     "actions": ["settle", "nudge"] if roster else ["settle"]})
    return sorted(rows, key=lambda row: row["idleSince"] or "")


def accounts(raw, context):
    from .account_room import project
    return project(raw, context)
