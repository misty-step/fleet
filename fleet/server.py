"""HTTP server: the page, the snapshot, a live event stream and the action endpoints.

Every route answers both with and without the configured base path, so the app works
behind `tailscale serve --set-path /fleet/` whether or not the proxy strips the prefix.
The page uses relative URLs for the same reason.
"""
import json
from urllib.parse import urlsplit
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "style.css": "text/css; charset=utf-8"}
KEEPALIVE_SECONDS = 15
MAX_BODY = 65536


def action_status(result):
    """200 on success; 409 when the action refused (codes starting "not-"); 502 when T3 failed."""
    if result.get("ok"):
        return HTTPStatus.OK
    return HTTPStatus.CONFLICT if str(result.get("error", "")).startswith("not-") else HTTPStatus.BAD_GATEWAY


def make_handler(hub, base_path, actions):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "fleet"

        def log_message(self, *args):
            pass

        def route(self):
            path = self.path.split("?", 1)[0]
            if base_path and (path == base_path or path.startswith(base_path + "/")):
                path = path[len(base_path):]
            return path or "/"

        def do_GET(self):
            path = self.route()
            if path == "/":
                path = "/index.html"
            name = path.lstrip("/")
            if name in STATIC:
                return self.send_body(HTTPStatus.OK, STATIC[name], (WEB / name).read_bytes())
            if path == "/api/snapshot":
                return self.send_body(HTTPStatus.OK, "application/json", hub.encoded.encode())
            if path == "/api/events":
                return self.stream()
            if path == "/healthz":
                return self.send_body(HTTPStatus.OK, "text/plain", b"ok\n")
            self.send_body(HTTPStatus.NOT_FOUND, "text/plain", b"not found\n")

        def do_POST(self):
            raw = self.read_body()
            path = self.route()
            prefix = "/api/actions/"
            if not path.startswith(prefix):
                return self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not-found"})
            # Cross-site pages cannot send a custom header without a CORS preflight, which we never grant.
            if self.headers.get("X-Fleet-Action") != "1":
                return self.send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "missing-header"})
            if not self.same_origin():
                return self.send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "cross-origin"})
            action = actions.get(path[len(prefix):])
            if action is None:
                return self.send_json(HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "unknown-action"})
            try:
                body = json.loads(raw or b"{}")
                server, thread = str(body["server"]), str(body["threadId"])
            except (ValueError, KeyError, TypeError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bad-request"})
            result = action(server, thread)
            self.send_json(action_status(result), result)

        def read_body(self):
            """Read the whole request body so a kept-alive connection stays in sync; refuse oversized ones."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.close_connection = True
                return None
            return self.rfile.read(length)

        def same_origin(self):
            """A browser's Origin, when sent, must name this host (directly or as the proxy forwarded it)."""
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            hosts = {self.headers.get("Host"), self.headers.get("X-Forwarded-Host")} - {None}
            return urlsplit(origin).netloc in hosts

        def stream(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            version = -1
            try:
                while True:
                    newer, encoded = hub.wait(version, KEEPALIVE_SECONDS)
                    if newer == version:
                        self.wfile.write(b": keepalive\n\n")
                    else:
                        version = newer
                        self.wfile.write(b"event: snapshot\ndata: " + encoded.encode() + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass

        def send_json(self, status, payload):
            self.send_body(status, "application/json", json.dumps(payload).encode())

        def send_body(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(hub, bind, port, base_path="", actions=None):
    httpd = ThreadingHTTPServer((bind, port), make_handler(hub, base_path, actions or {}))
    httpd.daemon_threads = True
    hub.start()
    httpd.serve_forever()
