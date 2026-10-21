"""T3 threads from every configured server, through each server's own MCP tools.

Partial shape: {"servers": [<snapshot server>], "threads": [<normalized thread>]}.
A normalized thread is:
  {"server", "threadId", "projectId", "project", "title", "status", "parentThreadId",
   "settled", "snoozed", "account", "model", "itemCount", "latestRunId",
   "createdAt", "updatedAt", "prs": [{"repository", "number", "url", "state"}],
   "pendingQuestions": int}
`pendingQuestions` is null when a waiting thread's questions could not be read.

Each server is collected on its own; one failing marks only that server and keeps
its last good threads.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from ..hub import describe, iso

PAGE = 100
MAX_PAGES = 200


def utc_now():
    return datetime.now(timezone.utc)


def stamp(value):
    """T3's millisecond ISO time as the contract's second-precision UTC string."""
    if not value:
        return None
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class T3:
    name = "t3"

    def __init__(self, config, clients, clock=utc_now):
        self.interval = config.interval_seconds
        self.clients = clients
        self.clock = clock
        self.last = {}           # server id -> {"lastSeenAt", "threads"} from its last good collection

    def collect(self, hub):
        ids = self.clients.ids()
        with ThreadPoolExecutor(max_workers=max(1, len(ids))) as pool:
            entries = list(pool.map(self.server, ids))
        return {"servers": [entry for entry, _ in entries],
                "threads": [thread for _, threads in entries for thread in threads]}

    def server(self, server_id):
        """(snapshot server entry, threads) for one server; never raises."""
        began = time.monotonic()
        try:
            threads = self.threads(server_id)
        except Exception as error:  # one server failing must not affect the others
            last = self.last.get(server_id, {"lastSeenAt": None, "threads": []})
            return ({"id": server_id, "ok": False, "error": describe(error), "lastSeenAt": last["lastSeenAt"],
                     "latencyMs": None, "threads": len(last["threads"])}, last["threads"])
        seen = iso(self.clock())
        self.last[server_id] = {"lastSeenAt": seen, "threads": threads}
        return ({"id": server_id, "ok": True, "error": None, "lastSeenAt": seen,
                 "latencyMs": round((time.monotonic() - began) * 1000), "threads": len(threads)}, threads)

    def threads(self, server_id):
        threads = []
        for project in self.pages(server_id, "t3_project_list", {}, "projects"):
            if project.get("deletedAt"):
                continue
            arguments = {"projectId": project["id"], "settled": False, "includeSubagents": True}
            for raw in self.pages(server_id, "t3_thread_list", arguments, "threads"):
                threads.append(self.normalize(server_id, project, raw))
        return threads

    def pages(self, server_id, tool, arguments, key):
        """Every item of a cursor-paginated list tool."""
        cursor = None
        for _ in range(MAX_PAGES):
            page = self.clients.call(server_id, tool, dict(arguments, limit=PAGE, cursor=cursor))
            yield from page.get(key) or []
            following = page.get("nextCursor")
            if following is None or following == cursor or not page.get(key):
                return
            cursor = following

    def normalize(self, server_id, project, raw):
        return {
            "server": server_id,
            "threadId": raw["threadId"],
            "projectId": project["id"],
            "project": project.get("title"),
            "title": raw.get("title"),
            "status": raw.get("status"),
            "parentThreadId": raw.get("parentThreadId"),
            "settled": bool(raw.get("settled")),
            "snoozed": bool(raw.get("snoozed")),
            "account": raw.get("providerInstanceId"),
            "model": raw.get("model"),
            "itemCount": raw.get("itemCount"),
            "latestRunId": raw.get("latestRunId"),
            "createdAt": stamp(raw.get("createdAt")),
            "updatedAt": stamp(raw.get("updatedAt")),
            "prs": prs(raw),
            "pendingQuestions": self.questions(server_id, raw) if raw.get("status") == "waiting" else 0,
        }

    def questions(self, server_id, raw):
        try:
            result = self.clients.call(server_id, "t3_pending_request_list", {"threadId": raw["threadId"]})
        except Exception:  # unknown, not fatal: the thread still shows as waiting
            return None
        return len(result.get("requestIds") or [])


def prs(raw):
    """Every linked pull request, including the legacy single link, once each."""
    linked = list(raw.get("pullRequests") or [])
    if raw.get("linkedPullRequest"):
        linked.append(raw["linkedPullRequest"])
    seen, result = set(), []
    for pr in linked:
        if not pr.get("url") or pr["url"] in seen:
            continue
        seen.add(pr["url"])
        result.append({"repository": pr.get("repository"), "number": pr.get("number"),
                       "url": pr["url"], "state": pr.get("state")})
    return result
