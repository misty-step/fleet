"""Pure rules that turn source data into the snapshot's lanes, attention, tidy and accounts.

Each function takes plain data and returns lists shaped as in docs/snapshot.md, so
every rule is testable without a network. Later PRs fill these in; until then each
returns an empty list and the page shows empty sections.
"""
from dataclasses import dataclass, field

ACTIVE = {"running", "starting", "preparing", "queued"}


@dataclass
class Context:
    threads: list            # normalized threads from the t3 source, children included
    roster: set              # harness wipRoots thread IDs
    now: object              # timezone-aware datetime
    thresholds: object       # config.Thresholds
    activity: dict = field(default_factory=dict)   # threadId -> {currentStep, lastSideEffect}
    rounds: dict = field(default_factory=dict)     # "owner/repo#number" -> {round, ...}


def lanes(context):
    return []


def attention(context, lanes):
    return []


def order(items):
    """Worst first: bad before warn, then the oldest condition first."""
    return sorted(items, key=lambda item: (item["severity"] != "bad", item.get("since") or ""))


def tidy(context):
    return []


def accounts(raw, context):
    return []
