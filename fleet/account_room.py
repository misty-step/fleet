"""Pure projection of Upkeep readings into the public account contract."""
import math
import re
from datetime import datetime, timezone

from .derive import ACTIVE

WEEK_HOURS = 7 * 24


def moment(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    return None


def iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def project(raw, context):
    result = []
    for account in raw:
        window = next((item for item in account.get("allowances", [])
                       if item.get("active") is True
                       and re.search(r"\bweekly\b", item.get("label") or "", re.IGNORECASE)), account)
        observed = (moment(window.get("observed_at")) or moment(account.get("observed_at"))
                    or moment(account.get("status_observed_at")))
        age = (context.now - observed).total_seconds() if observed else number(window.get("age_s"))
        if age is None:
            age = number(account.get("age_s"))
        if age is None:
            age = number(account.get("status_age_s"))
        if not observed and age is not None:
            evaluated = moment(account.get("evaluated_at"))
            if evaluated:
                age += max(0, (context.now - evaluated).total_seconds())
        stale_after = number(account.get("stale_after_s"))
        stale = age is not None and stale_after is not None and age > stale_after
        state = "stale" if stale else account.get("state") or "unknown"
        # Room and reset fall back per field to the account's own values when the weekly allowance lacks them.
        room = number(window.get("room_percent"))
        if room is None:
            room = number(account.get("room_percent"))
        reset = moment(window.get("resets_at")) or moment(account.get("resets_at"))
        measured = window.get("pace") or {}
        burn = number(measured.get("burn_pp_per_hour"))
        hours = (reset - context.now).total_seconds() / 3600 if reset else 0
        readable = not stale and state not in {"unknown", "stale"} and room is not None and room >= 0 and hours > 0
        sustainable = None
        pace = "unknown"
        if readable and burn is not None and burn >= 0:
            sustainable = room / hours
            pace = ("too_fast" if burn > sustainable * 1.25 else
                    "too_slow" if burn < sustainable * .75 else "on_pace")
        elif readable and burn is None:
            # No burn reading: room far above the on-pace target (hours to reset / window hours) is approximately too slow.
            target = hours / WEEK_HOURS * 100
            if room > target * 1.5 and room > target + 20:
                pace = "too_slow"
        runs_out = None
        if burn is not None and sustainable is not None and burn > sustainable and measured.get("outlasts_reset") is not True:
            runs_out = moment(measured.get("runs_out_at"))
        identifier = account.get("instance_id") or account.get("name")
        result.append({
            "id": identifier, "name": account.get("name"), "provider": account.get("provider"),
            "state": state, "roomPercent": room, "resetsAt": iso(reset), "pace": pace,
            "burnPerHour": burn, "sustainablePerHour": sustainable, "runsOutAt": iso(runs_out),
            "running": sum(thread.get("account") == identifier and thread.get("status") in
                           ACTIVE for thread in context.threads),
        })
    return result
