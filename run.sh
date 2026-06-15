#!/bin/zsh
# Scheduled entry point. launchd fires missed runs on wake, so guard the
# time window: only trade 15:30-15:58 ET on weekdays.
set -euo pipefail
cd "$(dirname "$0")"
. scripts/timezone.sh

host_tz="$(agentic_trader_detect_host_timezone)"
if ! agentic_trader_is_eastern_timezone "$host_tz"; then
  mkdir -p logs
  reason="$(agentic_trader_timezone_requirement_reason)"
  echo "$(date '+%F %T') WARN: host-TZ mismatch: refusing run; detected host timezone '$host_tz'. $reason" >> logs/runner.log
  exit 0
fi

export TZ=America/New_York
dow=$(date +%u) # 1=Mon
hm=$(date +%H%M)
if (( dow > 5 )) || (( hm < 1530 || hm > 1558 )); then
  echo "$(date '+%F %T') skip: outside trading window" >> logs/runner.log
  exit 0
fi

# Holiday / half-day skip: the trade window (15:30-15:58) is after a 13:00
# early close, so half-days are no-ops too. Guard before the lock so a
# holiday run is a clean no-op.
if ! python3 scripts/market_calendar.py --is-trading-day "$(date +%F)"; then
  echo "$(date '+%F %T') skip: market holiday/half-day" >> logs/runner.log
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
