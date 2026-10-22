"""A small pool of MCP sessions per configured T3 server, shared by every source and action.

A session serves one call at a time; up to SESSIONS calls to one server run at once,
each on its own session. A session that fails is dropped and a fresh one opened.
"""
import threading
import time

from .mcp import Mcp, McpError

SESSIONS = 8


class Clients:
    def __init__(self, servers, factory=Mcp, sessions=SESSIONS):
        self.servers = {server.id: server for server in servers}
        self.factory = factory
        self.idle = {server_id: [] for server_id in self.servers}   # sessions not serving a call
        self.slots = {server_id: threading.BoundedSemaphore(sessions) for server_id in self.servers}
        self.guard = threading.Lock()

    def ids(self):
        return list(self.servers)

    def call(self, server_id, tool, arguments, retry=True):
        """Call one tool on one server. Reopens the session once if it fails; raises McpError.

        Pass retry=False for a call that must not repeat, such as a send whose response may be lost.
        """
        server = self.servers.get(server_id)
        if server is None:
            raise McpError("unknown-server")
        with self.slots[server_id]:
            failed = False
            for attempt in (1, 2) if retry else (2,):
                with self.guard:   # a retry opens a fresh session: the idle ones may be as stale
                    session = self.idle[server_id].pop() if self.idle[server_id] and not failed else None
                try:
                    if session is None:
                        session = self.factory(server.url, server.token())
                    result = session.call(tool, arguments)
                except McpError as error:
                    if attempt == 2 or str(error).startswith("tool-"):
                        raise
                    failed = True
                    time.sleep(0.2)
                    continue
                with self.guard:
                    self.idle[server_id].append(session)
                return result
