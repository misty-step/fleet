# Fleet

One live page for a fleet of T3 Code agents: what needs attention, every lane's
state and last real side effect, review loops on pull requests, threads to tidy,
and how much room each AI account has left against its reset.

It reads each T3 server through that server's own MCP API, reads canonical account room
from [Upkeep](https://github.com/misty-step/upkeep)'s `room --json` command, and
streams a fresh snapshot to the page every few seconds.

```
python3 -m fleet serve --fixture fixtures/snapshot.json   # sample data at http://127.0.0.1:8797/
cp config.example.toml ~/.config/fleet/config.toml        # then edit servers and sources
packaging/install.sh                                      # install as a user service
```

Requires Python 3.12 or later and nothing else. See [SPEC.md](SPEC.md).
