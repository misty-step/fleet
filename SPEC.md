# Fleet

One live page that answers, at a glance: is any agent stuck, looping or wasting
account room? It shows every agent thread on every T3 server, the review loops
on their pull requests, and how much room each AI account has left.

## Why

phaedrus runs many agent lanes across several T3 servers. One agent looped
between review and fixes six times before anyone noticed. Upkeep's page is hard
to read at a glance, and T3 Code does not show Claude usage. Fleet is a separate
micro app that puts the whole fleet on one page and keeps it current within
seconds.

## The page, in order

1. **Needs attention.** A pull request at fix round 2 or later. A pending
   approval or question. A failed or interrupted run. A running lane with no
   real side effect (push, PR, merge, deploy, comment) for N hours.
2. **Lanes.** One row per top-level thread: goal (title), state, current step,
   last side effect, running children, PR rounds, account and model.
3. **Needs tidying.** Threads that are done but not settled, or stale. A
   one-click **Settle** button. **Nudge** appears only for roster lanes.
4. **Account room.** Every AI account: % left, reset time, and pace against the
   target of using every week down to zero right at reset (on pace, too fast,
   too slow). This replaces Upkeep's page for phaedrus; Upkeep stays the data
   source.

The page updates live, within seconds. Light and dark follow the system setting.
Design is neutral and plain, at the level of Linear, Vercel and GitHub.

## Stories

- **US-1 Live fleet.** As phaedrus, I open one tailnet URL and see every
  unsettled top-level thread on every T3 server, refreshed within 10 seconds
  without reloading. Each server shows whether it was reached and when.
- **US-2 Needs attention.** I see, first and worst first, every lane that is
  failed, interrupted, waiting on an approval or question for over 15 minutes,
  or quiet (running with no side effect for over 2 hours).
- **US-3 Review loops.** I see each pull request's review round, and any PR at
  fix round 2 or later is listed under Needs attention.
- **US-4 Lane detail.** Each lane row shows its current step, its last side
  effect and when, its running and total children, its PRs, and its account and
  model.
- **US-5 Tidy.** I see completed threads that are still unsettled (over 30
  minutes) and can settle one with a single click. Roster lanes also offer
  Nudge, which sends a short status request to the lane.
- **US-6 Account room.** I see every AI account's % left, reset time and pace
  verdict, with the accounts burning too fast or wasting room called out.
- **US-7 Inside T3.** I can keep the page open inside T3 Code, or I am told
  plainly why not.

## Rules

Thresholds are configuration; these are the defaults.

| Flag | Rule |
| --- | --- |
| Loop | A linked open PR is at fix round 2 or later, that is review round 3 or later (ship-loop allows one fix round) |
| Waiting | Thread status is `waiting` (approval or question) for over 15 min |
| Failed | Latest run `failed` |
| Interrupted | Latest run `interrupted` and the thread is not settled |
| Quiet | Running for over 2 h with no side effect in that time |
| Tidy | `completed`, unsettled and idle for over 30 min; or any unsettled thread idle for over 24 h |

Pace for an account window: `burn` is Upkeep's measured percentage points per
hour; `sustainable` is room left divided by hours to reset. On pace when burn is
within 25% of sustainable; too fast above that; too slow (room will be wasted)
below it. Without a burn reading but with room and reset known, Too slow is
approximate: room is over 1.5 times the on-pace target (hours to reset / 168) and over
20 points above it. Otherwise Unknown when Upkeep has no current reading.

## Architecture

```
T3 servers (mirrodin, serenity, cloud-home-agents)
   │  HTTP MCP over the tailnet, bearer token per server
   ▼
collector sources (fleet/sources/*) ──► assemble (fleet/snapshot.py) ──► hub
Upkeep status.json ─┘                                                  │
harness roster (wipRoots) ─┘                         SSE + JSON ◄──────┘
                                                        ▼
                                                  web page (web/)
```

- **Python 3.12 standard library only.** No dependencies, so it runs on any
  host with Python, including exe.dev VMs and macOS.
- **Reads go through T3's own MCP** (`t3_project_list`, `t3_thread_list`,
  `t3_thread_read`, `t3_pending_request_list`). Never the state database.
- **Writes go through T3's own MCP** (`t3_thread_update` to settle,
  `t3_thread_send` to nudge). Never the state database.
- **Account room comes from Upkeep's** `room --json` command, which supplies
  canonical account names, fresh room and reset observations. Unknown or expired
  observations show no current percentage. Probe success does not imply numeric room.
- **Remote-first.** The collector needs only network reach to each MCP endpoint
  and Upkeep, so it can run on mirrodin or a cloud VM. Configuration lives
  outside the repository; tokens arrive as environment variables from `pass`.
- **Tailnet only.** Served through `tailscale serve` at a path. Write endpoints
  require a same-origin request with the `X-Fleet-Action` header.

The snapshot contract between collector and page is in
[docs/snapshot.md](docs/snapshot.md); `fixtures/snapshot.json` is a synthetic
example that the page and tests use.

## Delivery

Each PR keeps main working and is worth merging alone.

1. Scaffold: server, live stream, snapshot contract, fixture page, CI, verify
   skill.
2. T3 source: servers, lanes, children, waiting/failed/interrupted attention,
   tidy list. (US-1, US-2, US-5 list)
3. Account room from Upkeep with pace verdicts. (US-6)
4. Page design for all four sections from the contract. (US-1 to US-6 display)
5. Activity: current step, last side effect, quiet flag. (US-2, US-4)
6. Review rounds from child titles and the PR comment ledger. (US-3)
7. Settle and Nudge through T3 MCP. (US-5 actions)
8. Deploy on the tailnet and report whether it can live inside T3. (US-7)
