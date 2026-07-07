#!/usr/bin/env bash
# uninstall.sh — remove all agentic-trader launchd schedules and the
# out-of-repo lane wrapper. The deploy worktree (~/src/agentic-trader-deploy)
# is left in place; remove it with `git worktree remove` if truly done.
set -euo pipefail

DEST="$HOME/Library/LaunchAgents"
WRAPPER="$HOME/.local/bin/agentic-trader-lane"
uid="$(id -u)"

for dst in "$DEST"/com.agentic-trader.*.plist; do
  [ -e "$dst" ] || continue
  label="$(basename "${dst%.plist}")"
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  rm -f "$dst"
  echo "removed $label"
done

if [ -e "$WRAPPER" ]; then
  rm -f "$WRAPPER"
  echo "removed wrapper $WRAPPER"
fi

echo "done"
