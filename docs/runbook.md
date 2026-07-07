# Runbook

Operating the trader day to day: installing schedules, checking health,
reading logs, and handling the two emergencies (kill-switch trip, Robinhood
connector death).

## Install / uninstall schedules

```sh
ops/install.sh      # refuses unless machine TZ is America/New_York
ops/uninstall.sh
```

`install.sh` substitutes the repo path into the plists in `ops/launchd/`,
copies them to `~/Library/LaunchAgents`, lints them, and bootstraps each
into `gui/$UID`. launchd `StartCalendarInterval` fires in the machine's
local timezone, which is why the TZ check is a hard refusal (override with
`--force` only if you enjoy trading at the wrong hours).

Verify: `launchctl list | grep agentic-trader` — six jobs.

A schedule missed while the Mac was asleep fires once on wake (launchd
coalesces missed StartCalendarInterval events); a powered-off window is
skipped entirely.

## Health checks

```sh
uv run trader lane ping                    # DB reachable?
uv run trader lane check research          # did today's lane complete? (per lane)
uv run trader sleeves status               # budgets, halts, drawdown
uv run trader kill-switch status           # account kill-switch state
uv run trader params show                  # current tunables vs envelope
docker compose ps                          # Postgres up?
```

The `lane_runs` table is the run ledger: every lane run has a row with
status `running` / `completed` / `failed`. A row stuck in `running` means
the process died mid-run — check the lane log.

## Logs

- `logs/lanes/<lane>-<timestamp>.log` — full Claude output per lane run.
- `logs/launchd/com.agentic-trader.<job>.log` — runner stdout/stderr per job.
- `state/artifacts/YYYY-MM-DD/<lane>.json` — the day's artifacts (mirror of
  the DB copy in `lane_runs.artifact`).

Failure notifications arrive as macOS notifications via `ops/notify.sh`.
The runner (`ops/run-lane.sh`) verifies each lane wrote a completed
`lane_runs` row — a lane that exits 0 without completing still notifies.

## Going live: ramp and flip

The full procedure (with verification checklists) is
`docs/go-live-checklist.md`. The short version:

```sh
# after a clean dress-rehearsal day with dry_run ON:
uv run trader ramp start          # week-1 half caps: 2.5%/position, 3 positions,
                                  # options sleeve latched halted (equity-only)
uv run trader dry-run off --reason "go-live day 1 at week-1 half caps"

uv run trader ramp options-on     # day 3: enable the options sleeve
uv run trader ramp full           # after 5 clean days: envelope defaults (5%/5)
```

All ramp changes go through the same envelope validation as
`trader params set` and are recorded in `param_history` (actor `human`).
`trader dry-run on --reason ...` is the instant retreat at any point —
gates immediately simulate instead of placing.

## Kill-switch trip (account 30% below HWM)

What happens automatically: gates block all new orders; RISK vetoes
everything; EXECUTION stops entering.

What you do:

1. Confirm: `uv run trader kill-switch status`.
2. Decide about open positions manually (the system will not add risk, but
   existing positions remain yours to manage).
3. Do NOT "fix" it by editing `trader/envelope.py` in anger. The switch is
   human-only by design; resetting requires an explicit human action
   (`trader kill-switch update --equity X` after depositing/reassessing, or
   accepting the new HWM baseline per M2's semantics).
4. Before resuming schedules, understand what drew down: read the week's
   review artifacts and grades.

Sleeve halt (10–25% sleeve drawdown) is the smaller version: the sleeve's
gate blocks its orders; the other sleeve continues. Same discipline.

## Robinhood MCP connector re-auth

The old system died silently for days on an expired connector auth. This
one fails loudly instead: lanes verify MCP connectivity at run start and
record a failed run → macOS notification.

When you get "Robinhood MCP unreachable" notifications:

1. Open Claude (desktop or `claude` CLI) and check the Robinhood connector
   status under connectors/MCP settings.
2. Re-authenticate the connector (it uses Robinhood's OAuth flow; expect
   MFA).
3. Sanity-check: start `claude` in this repo and fetch a SPY quote via the
   Robinhood MCP tool.
4. Missed EXECUTION runs do not auto-rerun; the next scheduled session
   picks up the queue. Manually run `ops/run-lane.sh execution` if the gap
   matters.

## Manual lane runs

```sh
ops/run-lane.sh research          # any single lane
ops/run-lane.sh chain-premarket   # research → thesis → risk
```

Safe to re-run: each run gets its own `lane_runs` row and log; downstream
lanes read the newest completed artifact for the day. EXECUTION re-runs
read today's execution artifacts to avoid double-entering a thesis.

## Postgres

```sh
docker compose up -d              # start (port 5433)
uv run trader db upgrade          # migrations
docker compose exec postgres pg_dump -U trader trader > backup.sql
```

On this machine the container runtime is **colima** (Docker Desktop was
wedged and would not launch as of 2026-07-06; `docker context ls` should
show `colima *`). Colima starts at login via `brew services start colima`,
and the Postgres container has `restart: unless-stopped`, so both survive
reboots. If Postgres is missing: `colima start && docker compose up -d`.

`DATABASE_URL` overrides the default local URL. If Postgres is down, every
lane fails pre-flight (`trader lane ping`) with a notification — lanes never
trade without state.
