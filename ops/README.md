# Ops

Runtime plumbing — human-only (agents may not edit anything in here):

- `run-lane.sh <lane|chain-premarket>` — pre-flight checks (uv, DB ping),
  invokes the lane via `claude -p`, logs to `logs/lanes/`, and verifies the
  lane wrote a completed `lane_runs` row (never trusts exit 0). Failures
  fire `notify.sh`.
- `notify.sh <title> <message>` — macOS notification via osascript;
  prints instead under `TRADER_TEST=1`.
- `launchd/*.plist` — schedule templates (`__REPO_ROOT__` placeholder),
  ET wall-clock times, machine must be on America/New_York.
- `install.sh` / `uninstall.sh` — validate timezone, substitute paths,
  copy to `~/Library/LaunchAgents`, bootstrap/bootout.

See `docs/lanes.md` for the schedule table and `docs/runbook.md` for
operations.
