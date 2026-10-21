"""The harness lane roster: `wipRoots` from t3-pool.json, read from a path or an https URL."""
import json
import urllib.request
from pathlib import Path


class Roster:
    name = "roster"
    interval = 60

    def __init__(self, location):
        self.location = location

    def collect(self, hub):
        if not self.location:
            return {"wipRoots": []}
        if self.location.startswith("https://"):
            with urllib.request.urlopen(self.location, timeout=10) as response:
                raw = json.load(response)
        else:
            raw = json.loads(Path(self.location).expanduser().read_text())
        return {"wipRoots": [str(item) for item in raw.get("wipRoots", [])]}
