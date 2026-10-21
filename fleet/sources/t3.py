"""T3 threads from every configured server, through each server's own MCP tools.

Partial shape: {"servers": [<snapshot server>], "threads": [<normalized thread>]}.
A normalized thread is:
  {"server", "threadId", "projectId", "project", "title", "status", "parentThreadId",
   "settled", "snoozed", "account", "model", "itemCount", "latestRunId",
   "createdAt", "updatedAt", "prs": [{"repository", "number", "url", "state"}],
   "pendingQuestions": int}
`pendingQuestions` is null when a waiting thread's questions could not be read.

Each server is collected on its own; one failing marks only that server and keeps
its last good threads. Each server has at most one request in flight, and a
collection waits for it only until a deadline; the request carries on into later
collections. Meanwhile the server shows its last result, or "slow" with its last
good threads once the request has run past SLOW_SECONDS or if it never finished.
"""
import threading
import time
from concurrent.futures import Future, wait
from datetime import datetime, timezone

from ..hub import describe, iso

PAGE = 100
MAX_PAGES = 200
MARGIN_SECONDS = 3
SLOW_SECONDS = 30


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
        self.deadline = config.interval_seconds + MARGIN_SECONDS   # None waits for every server
        self.slow_after = SLOW_SECONDS
        self.last = {}           # server id -> {"lastSeenAt", "threads"} from its last good collection
        self.running = {}        # server id -> (monotonic start, Future) of its request in flight
        self.finished = {}       # server id -> (entry, threads) of its last finished request

    def collect(self, hub):
        ids = self.clients.ids()
        for server_id in ids:
            self.harvest(server_id)
            if server_id not in self.running:
                self.running[server_id] = (time.monotonic(), self.start(server_id))
        wait([self.running[server_id][1] for server_id in ids], timeout=self.deadline)
        entries = [self.entry(server_id) for server_id in ids]
        return {"servers": [entry for entry, _ in entries],
                "threads": [thread for _, threads in entries for thread in threads]}

    def harvest(self, server_id):
        """Move a finished request's result out of flight."""
        if server_id in self.running and self.running[server_id][1].done():
            self.finished[server_id] = self.running.pop(server_id)[1].result()

    def entry(self, server_id):
        self.harvest(server_id)
        if server_id in self.running:
            began, _ = self.running[server_id]
            if server_id not in self.finished or time.monotonic() - began > self.slow_after:
                return self.failed(server_id, "slow")
        return self.finished[server_id]

    def start(self, server_id):
        """Collect one server on a daemon thread, so a hung server never holds up exit."""
        future = Future()
        threading.Thread(target=lambda: future.set_result(self.server(server_id)),
                         name="t3-" + server_id, daemon=True).start()
        return future

    def failed(self, server_id, error):
        last = self.last.get(server_id, {"lastSeenAt": None, "threads": []})
        return ({"id": server_id, "ok": False, "error": error, "lastSeenAt": last["lastSeenAt"],
                 "latencyMs": None, "threads": len(last["threads"])}, last["threads"])

    def server(self, server_id):
        """(snapshot server entry, threads) for one server; never raises."""
        began = time.monotonic()
        try:
            threads = self.threads(server_id)
        except Exception as error:  # one server failing must not affect the others
            return self.failed(server_id, describe(error))
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
