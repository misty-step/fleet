"""The harness lane roster: `wipRoots` from t3-pool.json, read from a path or an https URL."""
import json
import urllib.error
import urllib.request
from pathlib import Path

from ..hub import SourceError


class Roster:
    name = "roster"
    interval = 60

    def __init__(self, location):
        self.location = location

    def collect(self, hub):
        if not self.location:
            return {"wipRoots": []}
        try:
            if self.location.startswith("https://"):
                with urllib.request.urlopen(self.location, timeout=10) as response:
                    raw = json.load(response)
            else:
                raw = json.loads(Path(self.location).expanduser().read_text())
        except (OSError, urllib.error.URLError, TimeoutError):
            raise SourceError("roster-unavailable") from None
        except ValueError:
            raise SourceError("roster-invalid") from None
        roots = raw.get("wipRoots", []) if isinstance(raw, dict) else None
        if not isinstance(roots, list):
            raise SourceError("roster-invalid")
        return {"wipRoots": [str(item) for item in roots]}
