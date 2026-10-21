"""HTTP server: the page, the snapshot, a live event stream and the action endpoints.

Every route answers both with and without the configured base path, so the app works
behind `tailscale serve --set-path /fleet/` whether or not the proxy strips the prefix.
The page uses relative URLs for the same reason.
"""
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
STATIC = {"index.html": "text/html; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "style.css": "text/css; charset=utf-8"}
KEEPALIVE_SECONDS = 15


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
            path = self.route()
            prefix = "/api/actions/"
            if not path.startswith(prefix):
                return self.send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not-found"})
            # Cross-site pages cannot send a custom header without a CORS preflight, which we never grant.
            if self.headers.get("X-Fleet-Action") != "1":
                return self.send_json(HTTPStatus.FORBIDDEN, {"ok": False, "error": "missing-header"})
            action = actions.get(path[len(prefix):])
            if action is None:
                return self.send_json(HTTPStatus.NOT_IMPLEMENTED, {"ok": False, "error": "unknown-action"})
            try:
                length = min(int(self.headers.get("Content-Length") or 0), 65536)
                body = json.loads(self.rfile.read(length) or b"{}")
                server, thread = str(body["server"]), str(body["threadId"])
            except (ValueError, KeyError, TypeError):
                return self.send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "bad-request"})
            result = action(server, thread)
            self.send_json(HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_GATEWAY, result)

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
