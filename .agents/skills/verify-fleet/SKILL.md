---
name: verify-fleet
description: Verify a Fleet change end to end on the real page. Use before merging any Fleet PR, for QA of Fleet, or to prove the live deployment works.
---

# Verify Fleet

Fleet is a tailnet-only page with no sign-in. Production is the deployed
tailnet page; owner/admin sign-in is **n/a**, because the tailnet is the access
boundary and the app has no accounts or roles. No AgentMail QA account,
allowlist or Cloudflare Access entry is needed today. If Fleet ever gains
sign-in, the journey must first sign in to production with the agent's own
AgentMail QA account (credentials in `pass` at `workstation/<PROJECT>_QA_*`,
here `workstation/FLEET_QA_*`, read through `authenticated-commands`) and prove
the owner/admin role; missing access is reported as blocked, never replaced by
phaedrus's password. Instruction content lives in `AGENTS.md` only; create no
`CLAUDE.md`.

## 1. Checks

```
python3 -m unittest discover -s tests -v
node --check web/app.js
```

## 2. The page on sample data

```
python3 -m fleet serve --fixture fixtures/snapshot.json --port 0   # or a free port
```

Open it with T3's preview browser (`preview_open`, then `preview_snapshot` and a
screenshot) at 1280 px and 390 px, in light and dark. Confirm, in order: Needs
attention lists the loop, failed, waiting and quiet items worst first; every lane
row shows state, current step, last side effect, children and PR rounds; Needs
tidying shows Settle on every row and Nudge only on roster rows; Account room
shows % left, reset, pace and the on-pace marker. Nothing overflows at 390 px.

## 3. The real collector

On a host with a config (`~/.config/fleet/config.toml`) and pass access:

```
packaging/fleet-run   # or: pass-env run $(python3 -m fleet pass-env-args) -- python3 -m fleet snapshot
```

`fleet snapshot` must show every configured server `ok: true` and source health
`ok`. Compare the Account room rows with the same host’s
`upkeep room --json`: canonical provider/email names and T3 instance IDs agree,
missing weekly room stays unknown even when five-hour room exists, and an expired
observation never shows a current percentage. The private configuration uses
`upkeep_room_command`, an argument array, instead of the retired status URL. Compare two or three lanes against T3 (`t3_thread_list`) by hand.

## 4. Live deployment

Open the deployed URL (see the latest deployment note in the repository's
releases or PRs; on mirrodin it is the `/fleet/` path of its tailnet name).
Watch the status line: it must say Live and its age must stay under 10 seconds
for a full minute. Start or finish a disposable thread you own and confirm the
lane list changes within 10 seconds without reloading.

## 5. Actions

Settle: launch a disposable thread you own (title starting `Fleet QA`), let it
complete, wait for it under Needs tidying, click Settle and confirm in T3
(`t3_thread_list settled=true`) that it is settled. Never settle or nudge a
thread you do not own. Nudge is proven by tests with a fake MCP and by checking
it appears only on roster rows; do not nudge a real lane for QA.

## Evidence and cleanup

Attach screenshots and a short summary (revision, commands, results) to the PR
with `gh pr comment --body-file proof.md --attach shot.png`. Keep evidence out of
the repository. Settle any disposable thread you created and stop any local
server you started.
