"""Write actions on threads. Each goes through the owning T3 server's own MCP tools, never its database.

`build(config, clients, hub)` returns {name: callable(server_id, thread_id) -> {"ok": bool, "error": str|None}}.
An action runs only on a thread the hub's current snapshot lists under `tidy` with that action,
so the endpoint cannot act on arbitrary threads. Nudge also requires a roster lane.
"""
import threading

from .hub import describe

NUDGE_MESSAGE = ("Status check from Fleet: please reply briefly with what you are doing now, "
                 "what blocks you, and your next step.")


def build(config, clients, hub):
    if clients is None or not hasattr(hub, "partial"):
        return {}

    def listed(server_id, thread_id, action):
        return any(row.get("server") == server_id and row.get("threadId") == thread_id
                   and action in row.get("actions", []) for row in hub.current.get("tidy", []))

    def refresh():
        """Collect T3 once in the background so the page drops or updates the row within seconds."""
        for source in hub.sources:
            if source.name == "t3":
                threading.Thread(target=hub.run_once, args=(source,), name="refresh-t3", daemon=True).start()

    def call(server_id, tool, arguments, retry=True):
        try:
            clients.call(server_id, tool, arguments, retry=retry)
        except Exception as error:  # report a short code only, never a header or body
            return {"ok": False, "error": describe(error)}
        refresh()
        return {"ok": True, "error": None}

    def settle(server_id, thread_id):
        if not listed(server_id, thread_id, "settle"):
            return {"ok": False, "error": "not-tidy"}
        return call(server_id, "t3_thread_organize", {"threadId": thread_id, "action": "settle"})

    def nudge(server_id, thread_id):
        if not listed(server_id, thread_id, "nudge"):
            return {"ok": False, "error": "not-tidy"}
        if thread_id not in (hub.partial("roster") or {}).get("wipRoots", []):
            return {"ok": False, "error": "not-roster"}
        # One attempt only: the send has no idempotency key, so a replay after a lost response could deliver twice.
        return call(server_id, "t3_thread_send", {"threadId": thread_id, "message": NUDGE_MESSAGE, "mode": "auto"},
                    retry=False)

    return {"settle": settle, "nudge": nudge}
