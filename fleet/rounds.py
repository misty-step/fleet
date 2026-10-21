"""Review rounds per pull request, and the loop rule.

Rounds come from child thread titles and IDs (for example "round 2", "-r2") and,
when present, the ship-loop PR comment ledger.
Partial shape: {"owner/repo#number": {"round": int, "source": str}}.
Stub until the rounds PR lands.
"""


class Rounds:
    name = "rounds"
    interval = 30

    def __init__(self, config, clients):
        self.clients = clients

    def collect(self, hub):
        return {}


def apply(lanes, context):
    """Fill each lane PR's round from context.rounds."""


def attention(lanes, context):
    """Attention items of kind "loop"."""
    return []
