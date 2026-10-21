"""Write actions on threads. Each goes through the owning T3 server's own MCP tools, never its database.

`build(config)` returns {name: callable(server_id, thread_id) -> {"ok": bool, "error": str|None}}.
Settle and Nudge arrive in a later PR; until then there are none and the endpoints answer 501.
"""


def build(config):
    return {}
