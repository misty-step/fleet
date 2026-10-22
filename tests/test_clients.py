import threading
import time
import unittest

from fleet.clients import Clients
from fleet.mcp import McpError


class Server:
    id = "alpha"
    url = "https://alpha.example.test/mcp"

    def token(self):
        return "synthetic-token"


class Session:
    """Counts sessions and calls in flight; a call's tool names what it does."""

    def __init__(self, lock):
        self.lock = lock
        self.busy = False
        self.calls = 0

    def call(self, tool, arguments):
        with self.lock:
            assert not self.busy, "a session serves one call at a time"
            self.busy = True
            self.calls += 1
        time.sleep(arguments.get("sleep", 0))
        with self.lock:
            self.busy = False
        if tool == "fail":
            raise McpError("unreachable:URLError")
        return {"tool": tool}


def pool(sessions=3):
    lock, opened = threading.Lock(), []

    def factory(url, token):
        opened.append(Session(lock))
        return opened[-1]
    return Clients([Server()], factory=factory, sessions=sessions), opened


class ClientsTest(unittest.TestCase):
    def test_a_transport_failure_drops_every_idle_session_so_a_single_send_fails_at_most_once(self):
        clients, opened = pool(sessions=3)
        threads = [threading.Thread(target=clients.call, args=("alpha", "read", {"sleep": 0.05})) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(clients.idle["alpha"]), 3)
        for session in opened:   # the server restarted: every pooled session is now stale
            session.call = lambda tool, arguments: (_ for _ in ()).throw(McpError("http-404"))
        with self.assertRaises(McpError):
            clients.call("alpha", "t3_thread_send", {}, retry=False)
        self.assertEqual(clients.idle["alpha"], [])
        self.assertEqual(clients.call("alpha", "t3_thread_send", {}, retry=False), {"tool": "t3_thread_send"})

    def test_concurrent_calls_use_separate_sessions_up_to_the_bound(self):
        clients, opened = pool(sessions=3)
        threads = [threading.Thread(target=clients.call, args=("alpha", "read", {"sleep": 0.1})) for _ in range(9)]
        began = time.monotonic()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertLess(time.monotonic() - began, 0.6)
        self.assertEqual(len(opened), 3)
        self.assertEqual(sum(session.calls for session in opened), 9)

    def test_sessions_are_reused(self):
        clients, opened = pool()
        for _ in range(3):
            self.assertEqual(clients.call("alpha", "read", {}), {"tool": "read"})
        self.assertEqual((len(opened), opened[0].calls), (1, 3))

    def test_a_failing_session_is_dropped_and_the_call_retried_once(self):
        clients, opened = pool()
        with self.assertRaises(McpError):
            clients.call("alpha", "fail", {})
        self.assertEqual(len(opened), 2)
        clients.call("alpha", "read", {})
        self.assertEqual(len(opened), 3)
        with self.assertRaises(McpError):
            clients.call("alpha", "fail", {}, retry=False)
        self.assertEqual([session.calls for session in opened], [1, 1, 2])

    def test_a_retry_opens_a_fresh_session(self):
        clients, opened = pool()
        threads = [threading.Thread(target=clients.call, args=("alpha", "read", {"sleep": 0.1})) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(opened), 2)
        with self.assertRaises(McpError):
            clients.call("alpha", "fail", {})
        self.assertEqual(sorted(session.calls for session in opened), [1, 1, 2])   # one idle session tried, then a fresh one

    def test_unknown_server(self):
        clients, _ = pool()
        with self.assertRaisesRegex(McpError, "unknown-server"):
            clients.call("beta", "read", {})


if __name__ == "__main__":
    unittest.main()
