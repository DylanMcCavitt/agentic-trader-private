#!/bin/zsh
# Scheduled entry point. launchd fires missed runs on wake, so guard the
# time window: only trade 15:30-15:58 ET on weekdays.
set -euo pipefail
cd "$(dirname "$0")"

export TZ=America/New_York
dow=$(date +%u) # 1=Mon
hm=$(date +%H%M)
if (( dow > 5 )) || (( hm < 1530 || hm > 1558 )); then
  echo "$(date '+%F %T') skip: outside trading window" >> logs/runner.log
  exit 0
fi

lock=/tmp/agentic-trader.lock
if ! mkdir "$lock" 2>/dev/null; then
  echo "$(date '+%F %T') skip: already running" >> logs/runner.log
  exit 0
fi
trap 'rmdir "$lock"' EXIT

echo "$(date '+%F %T') run start" >> logs/runner.log
claude -p "Read TRADER.md and execute the daily trading run exactly as written." \
  --permission-mode dontAsk \
  --max-turns 40 \
  >> logs/runner.log 2>&1
echo "$(date '+%F %T') run end (exit $?)" >> logs/runner.log
