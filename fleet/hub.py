"""Runs each source on its own interval and publishes a fresh snapshot after every collection."""
import json
import threading
import time
from datetime import datetime, timezone

from . import snapshot


def utc_now():
    return datetime.now(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Hub:
    """Holds the latest partial from every source and the snapshot assembled from them."""

    def __init__(self, config, sources, clock=utc_now):
        self.config = config
        self.sources = list(sources)
        self.clock = clock
        self.partials = {}
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.current = snapshot.assemble({}, config, clock())
        self.encoded = json.dumps(self.current)

    def partial(self, name):
        """The latest data from one source, or None before its first success."""
        with self.lock:
            entry = self.partials.get(name)
            return entry["data"] if entry else None

    def run_once(self, source):
        started = self.clock()
        try:
            data = source.collect(self)
            entry = {"ok": True, "at": iso(started), "error": None, "data": data}
        except Exception as error:  # a source failure must never stop the others
            previous = self.partials.get(source.name) or {}
            entry = {"ok": False, "at": iso(started), "error": describe(error), "data": previous.get("data")}
        with self.lock:
            self.partials[source.name] = entry
            self.current = snapshot.assemble(dict(self.partials), self.config, self.clock())
            self.encoded = json.dumps(self.current)
            self.version += 1
            self.changed.notify_all()

    def start(self):
        for source in self.sources:
            threading.Thread(target=self._loop, args=(source,), name="source-" + source.name, daemon=True).start()

    def _loop(self, source):
        while True:
            began = time.monotonic()
            self.run_once(source)
            time.sleep(max(0.5, source.interval - (time.monotonic() - began)))

    def wait(self, version, timeout):
        """Block until a snapshot newer than `version` exists; return (version, encoded)."""
        with self.lock:
            self.changed.wait_for(lambda: self.version > version, timeout=timeout)
            return self.version, self.encoded


class FixedHub:
    """Serves one fixed snapshot; used for page work and tests."""

    def __init__(self, data):
        self.current = data
        self.encoded = json.dumps(data)
        self.version = 1

    def start(self):
        pass

    def wait(self, version, timeout):
        if version >= self.version:
            time.sleep(timeout)
        return self.version, self.encoded


def describe(error):
    """A short, loggable description: our own codes, otherwise only the class."""
    from .mcp import McpError
    return str(error)[:60] if isinstance(error, McpError) else type(error).__name__
