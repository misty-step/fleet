"""Collector sources. Each has a `name`, an `interval` in seconds and `collect(hub) -> dict`.

`collect` returns this source's partial (see snapshot.assemble) or raises; a failure
keeps the previous partial and marks the source unhealthy in the snapshot.
"""
from .. import activity, rounds
from ..clients import Clients
from .accounts import Accounts
from .roster import Roster
from .t3 import T3


def build(config, clients=None):
    """Every source this configuration enables."""
    clients = clients or Clients(config.servers)
    return [Roster(config.roster), T3(config, clients), Accounts(config.upkeep_room_command),
            activity.Activity(config, clients), rounds.Rounds(config, clients)]
