# Fleet

A live tailnet page showing every agent thread across the T3 servers, their
review loops, and AI account room. Read [SPEC.md](SPEC.md) for intent and
[docs/snapshot.md](docs/snapshot.md) for the contract between collector and page.

## Shape

- `fleet/sources/*`: collectors. Each returns a partial dict or raises.
- `fleet/derive.py`: pure rules from partials to lanes, attention, tidy, accounts.
- `fleet/snapshot.py`: assembles the published snapshot.
- `fleet/hub.py`, `fleet/server.py`: run sources, serve page, JSON and SSE.
- `fleet/actions.py`: Settle and Nudge, through T3 MCP only.
- `web/`: the page. Plain HTML, CSS and JavaScript, no build step.

## Rules

- Python 3.12 standard library only. No dependencies, no build step.
- Read and write T3 only through each server's own MCP tools. Never open a T3
  state database.
- Never log or print a token. Errors carry short codes, never headers or bodies.
- Commit no private data: no hostnames, thread titles, emails or tokens. Real
  configuration lives in `~/.config/fleet/config.toml`; fixtures are synthetic.
- Keep `fixtures/snapshot.json` a complete example of the contract; change the
  contract, fixture and page together.
- Design: neutral and plain, light and dark from the system, no decorative
  motion. The page refreshes every few seconds, so updates never animate.

## Check

```
python3 -m unittest discover -s tests -v
node --check web/app.js
python3 -m fleet serve --fixture fixtures/snapshot.json --port 8797   # page at http://127.0.0.1:8797/
```

Verify user-facing changes with [verify-fleet](.agents/skills/verify-fleet/SKILL.md).

## No fallbacks

No fallbacks, backup paths or masking safety nets. Write the one best solution: direct, clean, simple, elegant. If it fails, it must fail loudly and visibly so we can fix it. Security checks (auth, signatures, schema validation) stay, but fail loudly, never silently.
