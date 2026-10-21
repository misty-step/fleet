"""Per-lane activity: current step and last real side effect, and the quiet rule.

The source reads recent timeline items of active lanes with `t3_thread_read`.
Partial shape: {threadId: {"currentStep": str|None, "lastSideEffect": {...}|None}}.
Stub until the activity PR lands.
"""


class Activity:
    name = "activity"

    def __init__(self, config, clients):
        self.interval = config.interval_seconds
        self.clients = clients

    def collect(self, hub):
        return {}


def apply(lanes, context):
    """Fill each lane's currentStep and lastSideEffect from context.activity."""


def attention(lanes, context):
    """Attention items of kind "quiet"."""
    return []
