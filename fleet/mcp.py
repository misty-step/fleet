"""A minimal streamable-HTTP MCP client for one bearer-authenticated T3 server."""
import json
import urllib.error
import urllib.request

PROTOCOL = "2025-06-18"
TIMEOUT = 20


class McpError(Exception):
    """A short, loggable error code. Never carries headers or tokens."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_open = urllib.request.build_opener(_NoRedirect()).open


class Mcp:
    def __init__(self, url, token, timeout=TIMEOUT, opener=None):
        if not token:
            raise McpError("no-token")
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session = None
        self.sequence = 0
        self.open = opener or _open
        self.rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                "clientInfo": {"name": "fleet", "version": "1"}})
        self.rpc("notifications/initialized", {}, notify=True)

    def rpc(self, method, params, notify=False):
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notify:
            self.sequence += 1
            payload["id"] = self.sequence
        headers = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": PROTOCOL}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers)
        try:
            with self.open(request, timeout=self.timeout) as response:
                self.session = response.headers.get("Mcp-Session-Id", self.session)
                raw = response.read().decode()
        except urllib.error.HTTPError as error:
            raise McpError("http-" + str(error.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise McpError("unreachable:" + type(error).__name__) from None
        if notify or not raw.strip():
            return None
        if raw.lstrip().startswith("{"):
            frames = [json.loads(raw)]
        else:
            frames = [json.loads(line[5:]) for line in raw.splitlines() if line.startswith("data:")]
        data = next((frame for frame in frames if frame.get("id") == payload["id"]), None)
        if data is None:
            raise McpError("no-response")
        if "error" in data:
            raise McpError("rpc-" + str(data["error"].get("code")))
        return data.get("result")

    def call(self, name, arguments):
        """Call one tool and return its structured result."""
        result = self.rpc("tools/call", {"name": name, "arguments": arguments}) or {}
        content = result.get("structuredContent")
        if content is None:
            texts = [block.get("text", "") for block in result.get("content", []) if block.get("type") == "text"]
            try:
                content = json.loads(texts[0]) if len(texts) == 1 else {}
            except ValueError:
                content = {}
        if result.get("isError"):
            code = content.get("code") if isinstance(content, dict) else None
            raise McpError("tool-" + (code or "error"))
        return content
