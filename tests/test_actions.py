import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from fleet import actions, config, hub, server
from fleet.mcp import McpError

LANE = "mcp:lane-1"
CHILD = "mcp:child-1"
OTHER = "mcp:other-1"


class Roster:
    name, interval = "roster", 60

    def collect(self, _):
        return {"wipRoots": [LANE]}


class T3:
    name, interval = "t3", 5

    def __init__(self):
        self.collected = threading.Event()

    def collect(self, _):
        self.collected.set()
        return {"servers": [], "threads": []}


class FakeClients:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def call(self, server_id, tool, arguments):
        self.calls.append((server_id, tool, arguments))
        if self.error:
            raise self.error
        return {}


class ActionsTest(unittest.TestCase):
    def start(self, clients, tidy):
        self.t3 = T3()
        self.hub = hub.Hub(config.parse({}), [Roster(), self.t3])
        self.hub.run_once(self.hub.sources[0])
        self.hub.current = dict(self.hub.current, tidy=tidy)
        handler = server.make_handler(self.hub, "/fleet", actions.build(config.parse({}), clients, self.hub))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, action, server_id, thread_id):
        url = "http://127.0.0.1:%d/fleet/api/actions/%s" % (self.httpd.server_address[1], action)
        request = urllib.request.Request(url, data=json.dumps({"server": server_id, "threadId": thread_id}).encode(),
                                         headers={"X-Fleet-Action": "1"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def tidy(self):
        return [{"threadId": LANE, "server": "alpha", "roster": True, "actions": ["settle", "nudge"]},
                {"threadId": CHILD, "server": "alpha", "roster": False, "actions": ["settle"]}]

    def test_settle_calls_t3_and_refreshes(self):
        clients = FakeClients()
        self.start(clients, self.tidy())
        self.assertEqual(self.post("settle", "alpha", CHILD), (200, {"ok": True, "error": None}))
        self.assertEqual(clients.calls, [("alpha", "t3_thread_organize", {"threadId": CHILD, "action": "settle"})])
        self.assertTrue(self.t3.collected.wait(5))

    def test_nudge_sends_one_status_request(self):
        clients = FakeClients()
        self.start(clients, self.tidy())
        self.assertEqual(self.post("nudge", "alpha", LANE), (200, {"ok": True, "error": None}))
        [(server_id, tool, arguments)] = clients.calls
        self.assertEqual((server_id, tool, arguments["threadId"], arguments["mode"]), ("alpha", "t3_thread_send", LANE, "auto"))
        for words in ("doing", "blocks", "next step"):
            self.assertIn(words, arguments["message"])
        self.assertTrue(self.t3.collected.wait(5))

    def test_refuses_a_thread_not_listed_under_tidy(self):
        clients = FakeClients()
        self.start(clients, self.tidy())
        for action in ("settle", "nudge"):
            status, body = self.post(action, "alpha", OTHER)
            self.assertEqual((body["ok"], body["error"]), (False, "not-tidy"))
        self.assertEqual(self.post("settle", "beta", CHILD)[1]["error"], "not-tidy")
        self.assertEqual(clients.calls, [])

    def test_refuses_nudge_when_tidy_does_not_offer_it(self):
        clients = FakeClients()
        self.start(clients, self.tidy())
        self.assertEqual(self.post("nudge", "alpha", CHILD)[1], {"ok": False, "error": "not-tidy"})
        self.assertEqual(clients.calls, [])

    def test_refuses_nudge_for_a_thread_outside_the_roster(self):
        clients = FakeClients()
        self.start(clients, [{"threadId": OTHER, "server": "alpha", "roster": True, "actions": ["settle", "nudge"]}])
        self.assertEqual(self.post("nudge", "alpha", OTHER)[1], {"ok": False, "error": "not-roster"})
        self.assertEqual(clients.calls, [])

    def test_mcp_failure_returns_only_a_short_code(self):
        clients = FakeClients(McpError("http-401"))
        self.start(clients, self.tidy())
        status, body = self.post("settle", "alpha", CHILD)
        self.assertEqual((status, body), (502, {"ok": False, "error": "http-401"}))
        self.assertFalse(self.t3.collected.is_set())

    def test_unexpected_failure_reports_only_the_class(self):
        clients = FakeClients(RuntimeError("Authorization: Bearer secret-token"))
        self.start(clients, self.tidy())
        status, body = self.post("nudge", "alpha", LANE)
        self.assertEqual((status, body), (502, {"ok": False, "error": "RuntimeError"}))

    def test_fixture_mode_has_no_actions(self):
        self.assertEqual(actions.build(config.parse({}), None, hub.FixedHub({"tidy": []})), {})


if __name__ == "__main__":
    unittest.main()
