"""One MCP session per configured T3 server, shared by every source and action, reopened on failure."""
import threading
import time

from .mcp import Mcp, McpError


class Clients:
    def __init__(self, servers, factory=Mcp):
        self.servers = {server.id: server for server in servers}
        self.factory = factory
        self.sessions = {}
        self.locks = {server_id: threading.Lock() for server_id in self.servers}

    def ids(self):
        return list(self.servers)

    def call(self, server_id, tool, arguments, retry=True):
        """Call one tool on one server. Reopens the session once if it fails; raises McpError.

        Pass retry=False for a call that must not repeat, such as a send whose response may be lost.
        """
        server = self.servers.get(server_id)
        if server is None:
            raise McpError("unknown-server")
        with self.locks[server_id]:
            for attempt in (1, 2) if retry else (2,):
                try:
                    session = self.sessions.get(server_id)
                    if session is None:
                        session = self.sessions[server_id] = self.factory(server.url, server.token())
                    return session.call(tool, arguments)
                except McpError as error:
                    self.sessions.pop(server_id, None)
                    if attempt == 2 or str(error).startswith("tool-"):
                        raise
                    time.sleep(0.2)
