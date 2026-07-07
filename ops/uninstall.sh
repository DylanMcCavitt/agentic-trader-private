#!/usr/bin/env bash
# uninstall.sh — remove all agentic-trader launchd schedules.
set -euo pipefail

DEST="$HOME/Library/LaunchAgents"
uid="$(id -u)"

for dst in "$DEST"/com.agentic-trader.*.plist; do
  [ -e "$dst" ] || continue
  label="$(basename "${dst%.plist}")"
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  rm -f "$dst"
  echo "removed $label"
done

echo "done"
