"""Configuration: a TOML file outside the repository; tokens come from the environment."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("~/.config/fleet/config.toml").expanduser()


@dataclass(frozen=True)
class Server:
    id: str
    url: str
    token_env: str
    token_pass: str = ""     # pass entry that fills token_env; used only to build the service command

    def token(self):
        """The bearer token: the first line of the variable, as `pass` stores it."""
        value = os.environ.get(self.token_env, "").strip()
        return value.splitlines()[0].strip() if value else ""


@dataclass(frozen=True)
class Thresholds:
    quiet_hours: float = 2
    waiting_minutes: float = 15
    tidy_minutes: float = 30
    stale_hours: float = 24
    loop_round: int = 2

    def as_json(self):
        return {"quietHours": self.quiet_hours, "waitingMinutes": self.waiting_minutes,
                "tidyMinutes": self.tidy_minutes, "staleHours": self.stale_hours, "loopRound": self.loop_round}


@dataclass(frozen=True)
class Config:
    bind: str = "127.0.0.1"
    port: int = 8797
    base_path: str = "/fleet"
    interval_seconds: float = 5
    roster: str = "~/.config/t3-pool.json"
    upkeep_status_url: str = ""
    servers: tuple = ()
    thresholds: Thresholds = field(default_factory=Thresholds)
    fixture: str = ""


def load(path=None):
    path = Path(path).expanduser() if path else DEFAULT_PATH
    with open(path, "rb") as handle:
        raw = tomllib.load(handle)
    return parse(raw)


def parse(raw):
    servers = tuple(Server(id=s["id"], url=s["url"], token_env=s["token_env"], token_pass=s.get("token_pass", ""))
                    for s in raw.get("servers", []))
    thresholds = Thresholds(**raw.get("thresholds", {}))
    base = "/" + raw.get("base_path", "/fleet").strip("/")
    keys = {"bind", "port", "interval_seconds", "roster", "upkeep_status_url", "fixture"}
    plain = {key: raw[key] for key in keys if key in raw}
    return Config(base_path="" if base == "/" else base, servers=servers, thresholds=thresholds, **plain)
