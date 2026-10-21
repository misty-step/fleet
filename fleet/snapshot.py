"""Assemble the published snapshot (docs/snapshot.md) from the latest source partials."""
from datetime import timezone

from . import activity, derive, rounds

SCHEMA_VERSION = 1


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def data(partials, name, default):
    entry = partials.get(name)
    return entry["data"] if entry and entry.get("data") is not None else default


def assemble(partials, config, now):
    t3 = data(partials, "t3", {"servers": [], "threads": []})
    roster = set(data(partials, "roster", {}).get("wipRoots", []))
    context = derive.Context(
        threads=t3.get("threads", []), roster=roster, now=now, thresholds=config.thresholds,
        activity=data(partials, "activity", {}), rounds=data(partials, "rounds", {}))
    lanes = derive.lanes(context)
    activity.apply(lanes, context)
    rounds.apply(lanes, context)
    attention = derive.order(derive.attention(context, lanes) + activity.attention(lanes, context)
                             + rounds.attention(lanes, context))
    tidy = derive.tidy(context)
    accounts = derive.accounts(data(partials, "accounts", {}).get("accounts", []), context)
    flagged = {}
    for item in attention:
        flagged.setdefault(item["threadId"], set()).add(item["kind"])
    for lane in lanes:
        lane["flags"] = sorted(set(lane.get("flags", [])) | flagged.get(lane["threadId"], set()))
    kinds = [item["kind"] for item in attention]
    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso(now),
        "thresholds": config.thresholds.as_json(),
        "summary": {
            "lanes": len(lanes),
            "running": sum(1 for lane in lanes if lane["status"] in derive.ACTIVE),
            "loops": kinds.count("loop"),
            "waiting": kinds.count("waiting"),
            "stalled": sum(kinds.count(kind) for kind in ("failed", "interrupted", "quiet")),
            "tidy": len(tidy),
        },
        "servers": t3.get("servers", []),
        "attention": attention,
        "lanes": lanes,
        "tidy": tidy,
        "accounts": accounts,
        "sources": {name: {"ok": entry["ok"], "at": entry["at"], "error": entry["error"]}
                    for name, entry in sorted(partials.items())},
    }
