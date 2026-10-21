"""Incremental activity, lane side effects, and the quiet rule.

Commands recognized as deploys: wrangler deploy/publish, vercel (deploy or
--prod), fly/flyctl deploy, and netlify deploy, optionally via npx/bunx.
Native tools: push_files, create_pull_request, merge_pull_request,
add_issue_comment, create_pull_request_review, and deploy.
Only command input is summarized, never command output.
Partial: threadId -> {currentStep, lastSideEffect, runStartedAt}.
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


def command_text(text):
    """The timeline's first line is input; later lines may contain output."""
    first = (text or "").splitlines()[0] if text else ""
    first = first.removeprefix("$ ")
    try:
        words = shlex.split(first)
    except ValueError:
        return first
    if len(words) >= 3 and words[0].rsplit("/", 1)[-1] in {"bash", "sh", "zsh"}:
        if words[1] in {"-c", "-lc", "-ic"}:
            return words[2].splitlines()[0] if words[2] else ""
    return first


def command_kind(command):
    try:
        tokens = list(shlex.shlex(command, posix=True, punctuation_chars=";&|()"))
    except ValueError:
        return None
    segments, segment = [], []
    for token in tokens + [";"]:
        if token and all(c in ";&|()" for c in token):
            if segment:
                segments.append(segment)
            segment = []
        else:
            segment.append(token)
    for words in segments:
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
    """Return input-only step and side-effect kind from a summarized T3 item."""
    text = item.get("text") or ""
    kind = item.get("type")
    if kind == "command_execution":
        command = command_text(text)
        return command, command_kind(command)
    if kind == "assistant_message":
        return re.split(r"(?<=[.!?])\s|\n", text, maxsplit=1)[0], None
    if kind not in {"dynamic_tool", "tool_call"}:
        return None, None
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
        return None, None
    name = payload.get("toolName") or item.get("title") or "tool"
    inputs = payload.get("input") or payload.get("arguments") or payload
    if not isinstance(inputs, dict):
        inputs = {}
    command = inputs.get("cmd") or inputs.get("command")
    if isinstance(command, str):
        command = command_text(command)
        return command, command_kind(command)
    target = next((inputs[key] for key in ("threadId", "url", "repository", "path", "file_path")
                   if isinstance(inputs.get(key), str)), "")
    tool = re.split(r"[./]", name)[-1].removeprefix("mcp__github__")
    return (name + " " + target).strip(), TOOL_KINDS.get(tool)


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
        result = {}
        for thread in threads:
            key = thread["server"], thread["threadId"]
            state = self.threads.setdefault(key, {
                "marker": None, "cursor": max(0, thread.get("itemCount", 0) - LOOKBACK),
                "pending": {}, "stepPosition": -1, "runId": None,
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
                except Exception:
                    state = self.threads[key]
                else:
                    self.threads[key] = state
            result[key[1]] = dict(state["data"])
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
            step, kind = describe(item)
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
                        "kind": kind, "summary": summary(step, 80), "at": item["updatedAt"]}


def apply(lanes, context):
    """Use the lane's own latest step and its subtree's newest side effect."""
    children = {}
    for thread in context.threads:
        children.setdefault((thread["server"], thread.get("parentThreadId")), []).append(thread["threadId"])
    for lane in lanes:
        data = context.activity.get(lane["threadId"], {})
        lane["currentStep"] = data.get("currentStep")
        effects, seen, stack = [], set(), [lane["threadId"]]
        while stack:
            thread_id = stack.pop()
            if thread_id in seen:
                continue
            seen.add(thread_id)
            effect = context.activity.get(thread_id, {}).get("lastSideEffect")
            if effect and moment(effect.get("at")):
                effects.append(effect)
            stack.extend(children.get((lane["server"], thread_id), []))
        lane["lastSideEffect"] = max(effects, key=lambda e: moment(e["at"]), default=None)


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
        result.append({"kind": "quiet", "severity": "warn", "threadId": lane["threadId"],
                       "server": lane["server"], "title": lane["title"], "laneTitle": lane["title"],
                       "reason": "Running with no push, PR, merge, deploy or comment for " + duration,
                       "since": since.isoformat().replace("+00:00", "Z"), "prUrl": None})
    return result
