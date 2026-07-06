# Ops

Runtime plumbing — filled in M3:

- `run-lane.sh` — invokes a lane via `claude -p`, verifies completion, fires
  a macOS notification on failure
- launchd plists (one per schedule: premarket, 3x-daily execution,
  postmarket, weekly IMPROVE)
- `install.sh`, `notify.sh`, healthcheck
