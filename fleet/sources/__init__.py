"""Collector sources. Each has a `name`, an `interval` in seconds and `collect(hub) -> dict`.

`collect` returns this source's partial (see snapshot.assemble) or raises; a failure
keeps the previous partial and marks the source unhealthy in the snapshot.
"""
from .roster import Roster


def build(config):
    """Every source this configuration enables."""
    return [Roster(config.roster)]
