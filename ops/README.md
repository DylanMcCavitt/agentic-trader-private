# Ops

Runtime plumbing — human-only (agents may not edit anything in here):

- `run-lane.sh <lane|chain-premarket>` — pre-flight checks (uv, DB ping),
  invokes the lane via `claude -p`, logs to `logs/lanes/`, and verifies the
  lane wrote a completed `lane_runs` row (never trusts exit 0). Failures
  fire `notify.sh`.
- `notify.sh <title> <message>` — macOS notification via osascript;
  prints instead under `TRADER_TEST=1`.
- `launchd/*.plist` — schedule templates (`__WRAPPER__` / `__DEPLOY_ROOT__`
  placeholders), ET wall-clock times, machine must be on America/New_York.
- `deploy.sh` — create/refresh the deployment worktree at
  `~/src/agentic-trader-deploy` (detached at master) + `uv sync` + copy
  `config.local.json`. The scheduler runs from that worktree, never from
  the dev checkout. Run this to ship a new version to the scheduler.
- `lane-wrapper.sh` — template for `~/.local/bin/agentic-trader-lane`, the
  out-of-repo safety net launchd actually invokes; alarms via inline
  osascript if the deploy worktree's run-lane.sh is missing.
- `install.sh` / `uninstall.sh` — validate timezone, refresh the deploy
  worktree, install the wrapper, substitute paths, copy plists to
  `~/Library/LaunchAgents`, bootstrap/bootout.

See `docs/lanes.md` for the schedule table and `docs/runbook.md` for
operations.
