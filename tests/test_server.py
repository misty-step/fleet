import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

from fleet import config, hub, server, snapshot

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = json.loads((ROOT / "fixtures" / "snapshot.json").read_text())


class Running:
    def __init__(self, the_hub, base_path="/fleet", actions=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(the_hub, base_path, actions or {}))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as response:
            return response.status, response.headers.get("Content-Type"), response.read()

    def post(self, path, body, headers):
        request = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.app = Running(hub.FixedHub(FIXTURE))

    def tearDown(self):
        self.app.httpd.shutdown()

    def test_page_and_assets_with_and_without_base_path(self):
        for prefix in ("", "/fleet"):
            status, kind, body = self.app.get(prefix + "/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", kind)
            self.assertIn(b"<title>Fleet</title>", body)
            self.assertEqual(self.app.get(prefix + "/app.js")[0], 200)
            self.assertEqual(self.app.get(prefix + "/style.css")[0], 200)

    def test_snapshot_is_the_hub_snapshot(self):
        status, kind, body = self.app.get("/fleet/api/snapshot")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), FIXTURE)

    def test_event_stream_sends_the_snapshot(self):
        with urllib.request.urlopen(self.app.base + "/fleet/api/events", timeout=5) as response:
            self.assertEqual(response.headers.get("Content-Type"), "text/event-stream")
            self.assertEqual(response.readline(), b"event: snapshot\n")
            data = response.readline()
        self.assertEqual(json.loads(data[len(b"data: "):]), FIXTURE)

    def test_actions_require_the_header(self):
        status, body = self.app.post("/fleet/api/actions/settle", {"server": "a", "threadId": "t"}, {"Content-Type": "application/json"})
        self.assertEqual((status, body["error"]), (403, "missing-header"))

    def test_unknown_action(self):
        status, body = self.app.post("/fleet/api/actions/settle", {"server": "a", "threadId": "t"}, {"X-Fleet-Action": "1"})
        self.assertEqual((status, body["error"]), (501, "unknown-action"))

    def test_refusals_after_unread_bodies_keep_the_connection_usable(self):
        import http.client
        port = self.app.httpd.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        payload = json.dumps({"server": "a", "threadId": "t"})
        connection.request("POST", "/fleet/api/actions/settle", body=payload, headers={"Content-Type": "application/json"})
        self.assertEqual(connection.getresponse().read() and 403, 403)
        connection.request("GET", "/fleet/api/snapshot")
        response = connection.getresponse()
        self.assertEqual((response.status, json.loads(response.read())), (200, FIXTURE))
        connection.close()

    def test_cross_origin_is_refused(self):
        headers = {"X-Fleet-Action": "1", "Origin": "https://elsewhere.example"}
        status, body = self.app.post("/fleet/api/actions/settle", {"server": "a", "threadId": "t"}, headers)
        self.assertEqual((status, body["error"]), (403, "cross-origin"))

    def test_same_origin_through_a_proxy_is_allowed(self):
        app = Running(hub.FixedHub(FIXTURE), actions={"settle": lambda s, t: {"ok": True, "error": None}})
        headers = {"X-Fleet-Action": "1", "Origin": "https://fleet.example", "X-Forwarded-Host": "fleet.example"}
        try:
            status, _ = app.post("/fleet/api/actions/settle", {"server": "a", "threadId": "t"}, headers)
        finally:
            app.httpd.shutdown()
        self.assertEqual(status, 200)

    def test_refusal_and_failure_status_codes(self):
        self.assertEqual(server.action_status({"ok": False, "error": "not-tidy"}), 409)
        self.assertEqual(server.action_status({"ok": False, "error": "tool-error"}), 502)
        self.assertEqual(server.action_status({"ok": True, "error": None}), 200)

    def test_action_is_called(self):
        calls = []
        app = Running(hub.FixedHub(FIXTURE), actions={"settle": lambda s, t: calls.append((s, t)) or {"ok": True, "error": None}})
        try:
            status, body = app.post("/fleet/api/actions/settle", {"server": "alpha", "threadId": "mcp:x"}, {"X-Fleet-Action": "1"})
        finally:
            app.httpd.shutdown()
        self.assertEqual((status, body["ok"], calls), (200, True, [("alpha", "mcp:x")]))


class HubTest(unittest.TestCase):
    def test_failing_source_keeps_others_and_reports_error(self):
        class Good:
            name, interval = "roster", 60
            def collect(self, _):
                return {"wipRoots": ["mcp:a"]}

        class Bad:
            name, interval = "t3", 5
            def collect(self, _):
                raise RuntimeError("boom")

        now = datetime(2026, 10, 21, 23, 0, tzinfo=timezone.utc)
        the_hub = hub.Hub(config.parse({}), [Good(), Bad()], clock=lambda: now)
        for source in the_hub.sources:
            the_hub.run_once(source)
        sources = the_hub.current["sources"]
        self.assertTrue(sources["roster"]["ok"])
        self.assertEqual((sources["t3"]["ok"], sources["t3"]["error"]), (False, "RuntimeError"))
        self.assertEqual(the_hub.partial("roster"), {"wipRoots": ["mcp:a"]})
        self.assertEqual(the_hub.version, 2)

    def test_empty_snapshot_has_every_contract_key(self):
        empty = snapshot.assemble({}, config.parse({}), datetime(2026, 10, 21, tzinfo=timezone.utc))
        self.assertEqual(set(empty), set(FIXTURE))
        self.assertEqual(set(empty["summary"]), set(FIXTURE["summary"]))


class ConfigTest(unittest.TestCase):
    def test_example_config_parses(self):
        import tomllib
        parsed = config.parse(tomllib.loads((ROOT / "config.example.toml").read_text()))
        self.assertEqual(parsed.base_path, "/fleet")
        self.assertTrue(parsed.servers)
        self.assertEqual(parsed.thresholds.quiet_hours, 2)


if __name__ == "__main__":
    unittest.main()
