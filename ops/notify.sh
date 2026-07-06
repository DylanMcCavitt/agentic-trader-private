#!/usr/bin/env bash
# notify.sh <title> <message> — macOS notification. Dumb and reliable.
# Under TRADER_TEST=1 it prints instead of notifying (for tests).
set -u

TITLE="${1:-agentic-trader}"
MESSAGE="${2:-}"

if [ "${TRADER_TEST:-0}" = "1" ]; then
  echo "NOTIFY: ${TITLE}: ${MESSAGE}"
  exit 0
fi

# Escape double quotes for AppleScript; never let notification failure
# cascade into the caller's failure handling.
esc() { printf '%s' "$1" | sed 's/"/\\"/g'; }
osascript -e "display notification \"$(esc "$MESSAGE")\" with title \"$(esc "$TITLE")\"" || true
exit 0
