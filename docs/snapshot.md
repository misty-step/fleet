# Snapshot contract

The collector publishes one JSON snapshot. `GET <base>/api/snapshot` returns the
latest one; `GET <base>/api/events` streams each new one as a Server-Sent Event
named `snapshot`. The page renders only from this object. `fixtures/snapshot.json`
is a complete synthetic example.

Times are ISO 8601 UTC strings. Missing values are `null`, never omitted.

```jsonc
{
  "schemaVersion": 1,
  "generatedAt": "2026-10-21T23:00:00Z",
  "thresholds": { "quietHours": 2, "waitingMinutes": 15, "tidyMinutes": 30, "staleHours": 24, "loopRound": 2 },

  "summary": {
    "lanes": 20,          // rows in lanes
    "running": 12,        // lanes whose status is running/starting/preparing/queued
    "loops": 2,           // attention items of kind "loop"
    "waiting": 3,         // attention items of kind "waiting"
    "stalled": 4,         // attention items of kind failed, interrupted or quiet
    "tidy": 9             // rows in tidy
  },

  "servers": [{
    "id": "mirrodin",
    "ok": true,
    "error": null,                     // short error code when ok is false
    "lastSeenAt": "…",                 // last successful collection
    "latencyMs": 880,
    "threads": 120                     // unsettled threads seen, children included
  }],

  "attention": [{                      // worst first: bad before warn, then oldest
    "kind": "loop | waiting | failed | interrupted | quiet",
    "severity": "bad | warn",
    "threadId": "mcp:…",
    "server": "mirrodin",
    "title": "Tach",                   // thread title
    "laneTitle": "Tach",               // owning top-level lane title (same as title for lanes)
    "reason": "PR #338 is at fix round 3",
    "since": "…",                      // when this condition started, best known
    "prUrl": null                      // set for loops
  }],

  "lanes": [{                          // top-level unsettled threads, attention first then most recently updated
    "threadId": "mcp:…",
    "server": "mirrodin",
    "project": "harness",
    "title": "Process 2: verification everywhere",
    "roster": true,                    // threadId is in harness wipRoots
    "status": "running",               // T3 thread status
    "currentStep": "Recheck blocker on harness#447", // null until the activity source knows
    "lastSideEffect": {                // null when none seen
      "kind": "push | pr | merge | deploy | comment",
      "summary": "git push origin fix/447",
      "at": "…"
    },
    "children": { "running": 2, "total": 7 },
    "prs": [{
      "repository": "misty-step/harness",
      "number": 447,
      "url": "https://github.com/misty-step/harness/pull/447",
      "state": "open | merged | closed",
      "round": 2                       // null when unknown
    }],
    "account": "claude-mistystep",     // T3 provider instance id
    "model": "claude-opus-5-5",
    "updatedAt": "…",
    "flags": ["loop"]                  // attention kinds that apply to this lane or its children
  }],

  "tidy": [{
    "threadId": "…",
    "server": "mirrodin",
    "title": "Lage 10-21 22:36Z",
    "parentTitle": null,               // owning lane title for children
    "status": "completed",
    "reason": "Completed, not settled",
    "idleSince": "…",
    "roster": false,
    "actions": ["settle"]              // "settle" and, for roster lanes only, "nudge"
  }],

  "accounts": [{
    "id": "claude-mistystep",          // T3 provider instance id, else Upkeep name
    "name": "Claude Max 1",
    "provider": "anthropic | codex | google | grok | cursor | openrouter",
    "state": "current | stale | unknown | exhausted | metered",
    "roomPercent": 62.0,               // weekly room left; null when unknown
    "resetsAt": "…",
    "pace": "on_pace | too_fast | too_slow | unknown",
    "burnPerHour": 1.2,                // percentage points per hour
    "sustainablePerHour": 0.4,
    "runsOutAt": "…",                  // null when it outlasts the reset
    "running": 3                       // running threads on this account across the fleet
  }],

  "sources": {                         // per-source health
    "t3": { "ok": true, "at": "…", "error": null },
    "accounts": { "ok": true, "at": "…", "error": null }
  }
}
```

## Actions

`POST <base>/api/actions/settle` and `POST <base>/api/actions/nudge` take
`{"server": "...", "threadId": "..."}`, require the `X-Fleet-Action: 1` header
and a same-origin `Origin`, and return `{"ok": true}` or
`{"ok": false, "error": "..."}`. They call T3's own MCP tools.
