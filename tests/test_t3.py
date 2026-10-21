import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

from fleet import config, derive, hub, rounds, snapshot
from fleet.mcp import McpError
from fleet.sources import t3
from fleet.sources.t3 import T3

NOW = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)


def ago(minutes):
    return (NOW - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%S.123Z")


def raw_thread(thread_id, status="running", parent=None, updated=1, **extra):
    thread = {"threadId": thread_id, "title": "T " + thread_id, "status": status, "parentThreadId": parent,
              "settled": False, "snoozed": False, "providerInstanceId": "acct", "model": "m", "itemCount": 3,
              "latestRunId": "run:" + thread_id, "createdAt": ago(600), "updatedAt": ago(updated),
              "pullRequests": [], "linkedPullRequest": None}
    thread.update(extra)
    return thread


class FakeClients:
    """Pages lists two items at a time, as T3 does with `nextCursor`."""

    def __init__(self, servers, pending=None, failing=()):
        self.servers = servers            # id -> {project: [raw threads]}
        self.pending = pending or {}      # threadId -> request IDs
        self.failing = set(failing)
        self.hanging = {}                 # server id -> Event its calls block on until set
        self.broken = set()               # project names whose thread list fails
        self.delay = 0                    # seconds each thread list call takes
        self.calls = []
        self.lock = threading.Lock()
        self.active = self.most = 0       # calls in flight now, and at most

    def ids(self):
        return list(self.servers)

    def call(self, server_id, tool, arguments):
        self.calls.append((server_id, tool, arguments))
        if server_id in self.hanging:
            self.hanging[server_id].wait()
        if server_id in self.failing:
            raise McpError("unreachable:URLError")
        projects = self.servers[server_id]
        if tool == "t3_project_list":
            items, key = [{"id": "p-" + name, "title": name, "deletedAt": None} for name in projects], "projects"
        elif tool == "t3_thread_list":
            assert (arguments["settled"], arguments["includeSubagents"], arguments["limit"]) == (False, True, 100)
            if arguments["projectId"][2:] in self.broken:
                raise McpError("tool-error")
            with self.lock:
                self.active += 1
                self.most = max(self.most, self.active)
            time.sleep(self.delay)
            with self.lock:
                self.active -= 1
            items, key = projects[arguments["projectId"][2:]], "threads"
        elif tool == "t3_pending_request_list":
            return {"requestIds": self.pending.get(arguments["threadId"], [])}
        start = arguments.get("cursor") or 0
        following = start + 2 if start + 2 < len(items) else None
        return {key: items[start:start + 2], "nextCursor": following}


def collect(clients):
    return T3(config.parse({}), clients, clock=lambda: NOW).collect(None)


def thread(thread_id, status="running", parent=None, updated=1, server="alpha", **extra):
    base = {"server": server, "threadId": thread_id, "projectId": "p-x", "project": "x", "title": "T " + thread_id,
            "status": status, "parentThreadId": parent, "settled": False, "snoozed": False, "account": "acct",
            "model": "m", "itemCount": 3, "latestRunId": None, "createdAt": None,
            "updatedAt": (NOW - timedelta(minutes=updated)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prs": [], "pendingQuestions": 0}
    base.update(extra)
    return base


def context(threads, roster=()):
    return derive.Context(threads=threads, roster=set(roster), now=NOW, thresholds=config.Thresholds())


class SourceTest(unittest.TestCase):
    def test_follows_cursors_for_projects_and_threads(self):
        clients = FakeClients({"alpha": {"a": [raw_thread("t%d" % n) for n in range(5)], "b": [], "c": [raw_thread("c1")]}})
        partial = collect(clients)
        self.assertEqual(sorted(t["threadId"] for t in partial["threads"]), ["c1", "t0", "t1", "t2", "t3", "t4"])
        self.assertEqual(sum(1 for call in clients.calls if call[1] == "t3_project_list"), 2)
        self.assertEqual([call[2]["cursor"] for call in clients.calls if call[2].get("projectId") == "p-a"], [None, 2, 4])

    def test_normalizes_threads(self):
        pr = {"host": "github.com", "repository": "o/r", "number": 7, "url": "https://github.com/o/r/pull/7",
              "source": "x", "state": "open"}
        clients = FakeClients({"alpha": {"proj": [raw_thread("t1", parent="lane", pullRequests=[pr], linkedPullRequest=pr)]}})
        [normalized] = collect(clients)["threads"]
        self.assertEqual(normalized, {
            "server": "alpha", "threadId": "t1", "projectId": "p-proj", "project": "proj", "title": "T t1",
            "status": "running", "parentThreadId": "lane", "settled": False, "snoozed": False, "account": "acct",
            "model": "m", "itemCount": 3, "latestRunId": "run:t1", "createdAt": "2026-10-21T13:00:00Z",
            "updatedAt": "2026-10-21T22:59:00Z",
            "prs": [{"repository": "o/r", "number": 7, "url": "https://github.com/o/r/pull/7", "state": "open"}],
            "pendingQuestions": 0})

    def test_pending_questions_only_for_waiting_threads(self):
        clients = FakeClients({"alpha": {"p": [raw_thread("w", status="waiting"), raw_thread("r")]}}, pending={"w": ["q1", "q2"]})
        counts = {t["threadId"]: t["pendingQuestions"] for t in collect(clients)["threads"]}
        self.assertEqual(counts, {"w": 2, "r": 0})
        self.assertEqual([call[2] for call in clients.calls if call[1] == "t3_pending_request_list"], [{"threadId": "w"}])

    def test_failing_server_keeps_its_last_threads_and_others_are_unaffected(self):
        clients = FakeClients({"alpha": {"p": [raw_thread("a1")]}, "beta": {"p": [raw_thread("b1"), raw_thread("b2")]}})
        source = T3(config.parse({}), clients, clock=lambda: NOW)
        source.collect(None)
        clients.failing.add("beta")
        clients.servers["alpha"]["p"].append(raw_thread("a2"))
        partial = source.collect(None)
        alpha, beta = partial["servers"]
        self.assertEqual((alpha["id"], alpha["ok"], alpha["error"], alpha["threads"]), ("alpha", True, None, 2))
        self.assertIsInstance(alpha["latencyMs"], int)
        self.assertEqual(beta, {"id": "beta", "ok": False, "error": "unreachable:URLError",
                                "lastSeenAt": "2026-10-21T23:00:00Z", "latencyMs": None, "threads": 2})
        self.assertEqual(sorted(t["threadId"] for t in partial["threads"]), ["a1", "a2", "b1", "b2"])

    def test_hung_server_does_not_hold_back_the_others(self):
        clients = FakeClients({"alpha": {"p": [raw_thread("a1")]}, "beta": {"p": [raw_thread("b1")]}})
        source = T3(config.parse({}), clients, clock=lambda: NOW)
        source.deadline = 0.2
        the_hub = hub.Hub(config.parse({}), [source], clock=lambda: NOW)
        the_hub.run_once(source)
        release = clients.hanging["beta"] = threading.Event()
        clients.servers["alpha"]["p"].append(raw_thread("a2"))
        try:
            for slow_after, beta_ok in ((60, True), (0, False), (0, False)):
                source.slow_after = slow_after
                began = time.monotonic()
                the_hub.run_once(source)
                self.assertLess(time.monotonic() - began, 2)
                published = the_hub.current
                alpha, beta = published["servers"]
                self.assertEqual((alpha["ok"], alpha["threads"]), (True, 2))
                self.assertEqual((beta["ok"], beta["lastSeenAt"], beta["threads"]), (beta_ok, "2026-10-21T23:00:00Z", 1))
                if not beta_ok:
                    self.assertEqual((beta["error"], beta["latencyMs"]), ("slow", None))
                self.assertEqual(sorted(lane["threadId"] for lane in published["lanes"]), ["a1", "a2", "b1"])
            # one request in flight per server: the hung one is never started twice
            self.assertEqual(sum(1 for call in clients.calls if call[:2] == ("beta", "t3_project_list")), 2)
        finally:
            release.set()
        del clients.hanging["beta"]
        clients.servers["beta"]["p"].append(raw_thread("b2"))
        source.deadline = 2
        source.collect(None)
        partial = source.collect(None)
        self.assertEqual((partial["servers"][1]["ok"], partial["servers"][1]["threads"]), (True, 2))

    def test_lists_projects_concurrently_with_bounded_workers(self):
        clients = FakeClients({"alpha": {"p%d" % n: [raw_thread("t%d" % n)] for n in range(40)}})
        clients.delay = 0.1   # one at a time this server takes 4 s
        began = time.monotonic()
        partial = collect(clients)
        self.assertLess(time.monotonic() - began, 1.5)
        self.assertEqual(clients.most, t3.WORKERS)
        self.assertEqual([t["threadId"] for t in partial["threads"]], ["t%d" % n for n in range(40)])
        self.assertEqual(partial["servers"][0]["threads"], 40)

    def test_one_failing_project_fails_its_server(self):
        clients = FakeClients({"alpha": {"a": [raw_thread("a1")], "b": [raw_thread("b1")]}, "beta": {"p": [raw_thread("c1")]}})
        clients.broken.add("b")
        alpha, beta = collect(clients)["servers"]
        self.assertEqual((alpha["ok"], alpha["error"]), (False, "tool-error"))
        self.assertEqual((beta["ok"], beta["threads"]), (True, 1))

    def test_pr_without_a_state_counts_as_open(self):
        linked = [{"repository": "o/r", "number": n, "url": "https://github.com/o/r/pull/%d" % n, "state": state}
                  for n, state in ((1, None), (2, "merged"), (3, "closed"), (4, "open"))]
        partial = collect(FakeClients({"alpha": {"p": [raw_thread("t1", pullRequests=linked)]}}))
        self.assertEqual([pr["state"] for pr in partial["threads"][0]["prs"]], ["open", "merged", "closed", "open"])
        self.assertEqual([pr["number"] for pr in rounds.open_prs(partial["threads"])], [1, 4])

    def test_server_never_reached(self):
        partial = collect(FakeClients({"alpha": {}}, failing={"alpha"}))
        self.assertEqual(partial["servers"][0]["lastSeenAt"], None)
        self.assertEqual(partial["threads"], [])


class LanesTest(unittest.TestCase):
    def test_one_row_per_top_level_thread_with_descendants(self):
        pr = {"repository": "o/r", "number": 7, "url": "u", "state": "open"}
        threads = [thread("lane", prs=[pr]), thread("c1", parent="lane"), thread("c2", "completed", parent="lane"),
                   thread("g1", "waiting", parent="c2"), thread("g2", "idle", parent="c1"),
                   thread("lane", server="beta"), thread("orphan", parent="settled-lane")]
        rows = derive.lanes(context(threads, roster={"lane"}))
        self.assertEqual([(row["server"], row["threadId"]) for row in rows], [("alpha", "lane"), ("beta", "lane")])
        row = rows[0]
        self.assertEqual(row["children"], {"running": 2, "total": 4})
        self.assertEqual(row["prs"], [dict(pr, round=None)])
        self.assertEqual((row["roster"], row["currentStep"], row["lastSideEffect"]), (True, None, None))
        self.assertEqual(rows[1]["children"], {"running": 0, "total": 0})
        self.assertEqual(set(row), {"threadId", "server", "project", "title", "roster", "status", "currentStep",
                                    "lastSideEffect", "children", "prs", "account", "model", "updatedAt", "flags"})

    def test_attention_first_then_most_recently_updated(self):
        threads = [thread("old", updated=50), thread("new", updated=1), thread("failed", "failed", updated=90),
                   thread("quiet-parent", updated=80), thread("child", "interrupted", parent="quiet-parent")]
        rows = derive.lanes(context(threads))
        self.assertEqual([row["threadId"] for row in rows], ["quiet-parent", "failed", "new", "old"])
        self.assertEqual(rows[0]["flags"], ["interrupted"])

    def test_snapshot_reorders_after_later_flags(self):
        partials = {"t3": {"data": {"servers": [], "threads": [thread("a", updated=1), thread("b", updated=5)]},
                           "ok": True, "at": None, "error": None}}
        result = snapshot.assemble(partials, config.parse({}), NOW)
        self.assertEqual([lane["threadId"] for lane in result["lanes"]], ["a", "b"])
        self.assertEqual(derive.order_lanes([{"threadId": "a", "updatedAt": "2", "flags": []},
                                             {"threadId": "b", "updatedAt": "1", "flags": ["quiet"]}])[0]["threadId"], "b")


class AttentionTest(unittest.TestCase):
    def kinds(self, threads):
        return [(item["threadId"], item["kind"], item["severity"]) for item in derive.attention(context(threads), [])]

    def test_waiting_over_threshold_is_a_warning_with_its_reason(self):
        threads = [thread("q", "waiting", updated=67, pendingQuestions=1), thread("a", "waiting", updated=20),
                   thread("u", "waiting", updated=16, pendingQuestions=None), thread("short", "waiting", updated=10)]
        items = {item["threadId"]: item for item in derive.attention(context(threads), [])}
        self.assertEqual(sorted(items), ["a", "q", "u"])
        self.assertEqual(items["q"]["reason"], "Waiting on a question for 1 h 7 min")
        self.assertEqual(items["a"]["reason"], "Waiting on an approval for 20 min")
        self.assertEqual(items["u"]["reason"], "Waiting on an approval or question for 16 min")
        self.assertEqual((items["q"]["severity"], items["q"]["since"], items["q"]["prUrl"]),
                         ("warn", "2026-10-21T21:53:00Z", None))

    def test_failed_and_interrupted_are_bad(self):
        self.assertEqual(self.kinds([thread("f", "failed"), thread("i", "interrupted"), thread("r"), thread("c", "completed")]),
                         [("f", "failed", "bad"), ("i", "interrupted", "bad")])

    def test_snoozed_threads_are_excluded(self):
        self.assertEqual(self.kinds([thread("f", "failed", snoozed=True), thread("w", "waiting", updated=99, snoozed=True)]), [])

    def test_children_carry_their_lane_title(self):
        threads = [thread("lane"), thread("mid", parent="lane"), thread("deep", "failed", parent="mid"),
                   thread("orphan", "failed", parent="gone"), thread("lane", "failed")]
        titles = {item["threadId"]: (item["title"], item["laneTitle"]) for item in derive.attention(context(threads), [])}
        self.assertEqual(titles, {"deep": ("T deep", "T lane"), "orphan": ("T orphan", None), "lane": ("T lane", "T lane")})


class TidyTest(unittest.TestCase):
    def test_rules_reasons_and_actions(self):
        threads = [thread("lane", "completed", updated=31), thread("cancelled", "cancelled", updated=40),
                   thread("fresh", "completed", updated=29), thread("stale", "idle", updated=24 * 60 + 1),
                   thread("young", "failed", updated=23 * 60), thread("old-failed", "interrupted", updated=25 * 60),
                   thread("active", "running", updated=3000), thread("waiting", "waiting", updated=3000),
                   thread("snoozed", "completed", updated=99, snoozed=True), thread("child", "completed", parent="lane", updated=60)]
        rows = {row["threadId"]: row for row in derive.tidy(context(threads, roster={"stale", "lane"}))}
        self.assertEqual(sorted(rows), ["cancelled", "child", "lane", "old-failed", "stale"])
        self.assertEqual(rows["lane"]["reason"], "Completed, not settled")
        self.assertEqual(rows["cancelled"]["reason"], "Completed, not settled")
        self.assertEqual(rows["stale"]["reason"], "Idle for over 24 h")
        self.assertEqual(rows["old-failed"]["reason"], "Idle for over 24 h")
        self.assertEqual((rows["stale"]["actions"], rows["lane"]["actions"], rows["child"]["actions"]),
                         (["settle", "nudge"], ["settle", "nudge"], ["settle"]))
        self.assertEqual((rows["child"]["parentTitle"], rows["lane"]["parentTitle"]), ("T lane", None))
        self.assertEqual(rows["stale"]["idleSince"], "2026-10-20T22:59:00Z")
        self.assertEqual(set(rows["lane"]), {"threadId", "server", "title", "parentTitle", "status", "reason",
                                             "idleSince", "roster", "actions"})

    def test_oldest_idle_first(self):
        threads = [thread("a", "completed", updated=40), thread("b", "completed", updated=90)]
        self.assertEqual([row["threadId"] for row in derive.tidy(context(threads))], ["b", "a"])


if __name__ == "__main__":
    unittest.main()
