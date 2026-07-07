#!/usr/bin/env bash
# run-lane.sh <lane|chain-premarket>
#
# Runs one headless Claude Code lane (or the premarket research->thesis->risk
# chain). Success is verified by the lane having written a completed
# `lane_runs` row (via `trader lane check`), NOT by the claude exit code —
# the old system died silently on exit 0; this one notifies instead.
#
# Test/override hooks (all optional env vars):
#   TRADER_CLAUDE_CMD   command run instead of `claude` (receives -p <prompt> args)
#   TRADER_CHECK_CMD    command run instead of `uv run trader lane check <lane>`
#   TRADER_PING_CMD     command run instead of `uv run trader lane ping`
#   TRADER_TEST=1       makes notify.sh print instead of notifying
#   TRADER_LOG_DIR      lane log directory (default $REPO_ROOT/logs/lanes);
#                       tests set this so pytest never writes real lane logs
set -uo pipefail

# launchd jobs get a minimal PATH; claude installs to ~/.local/bin.
export PATH="$HOME/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
NOTIFY="$SCRIPT_DIR/notify.sh"
LANES=(research thesis risk execution review improve)

usage() {
  echo "usage: run-lane.sh <research|thesis|risk|execution|review|improve|chain-premarket>" >&2
  exit 2
}

[ $# -eq 1 ] || usage
TARGET="$1"

fail() { # fail <lane> <reason>
  bash "$NOTIFY" "agentic-trader: $1 lane FAILED" "$2"
  echo "FAIL [$1]: $2" >&2
  exit 1
}

preflight() { # preflight <lane>
  local lane="$1"
  [ -f "$REPO_ROOT/lanes/$lane.md" ] || fail "$lane" "missing prompt lanes/$lane.md"
  command -v uv >/dev/null 2>&1 || fail "$lane" "uv not on PATH"
  if [ -n "${TRADER_PING_CMD:-}" ]; then
    $TRADER_PING_CMD >/dev/null 2>&1 || fail "$lane" "database unreachable (ping failed)"
  else
    (cd "$REPO_ROOT" && uv run trader lane ping >/dev/null 2>&1) \
      || fail "$lane" "database unreachable (trader lane ping failed)"
  fi
  # Robinhood MCP connectivity is verified inside the lane itself: each
  # lane that needs it makes an MCP call early and fails loudly if it
  # can't (see lanes/*.md), which this runner catches via the check below.
}

run_one() { # run_one <lane>
  local lane="$1"
  preflight "$lane"

  local log_dir="${TRADER_LOG_DIR:-$REPO_ROOT/logs/lanes}"
  mkdir -p "$log_dir"
  local stamp log
  stamp="$(date +%Y-%m-%d-%H%M%S)"
  log="$log_dir/$lane-$stamp.log"
  echo "[run-lane] $lane starting at $stamp, log: $log"

  local claude_cmd=(claude -p --permission-mode acceptEdits)
  if [ -n "${TRADER_CLAUDE_CMD:-}" ]; then
    # shellcheck disable=SC2206
    claude_cmd=($TRADER_CLAUDE_CMD)
  fi

  local exit_code=0
  (cd "$REPO_ROOT" && "${claude_cmd[@]}" "$(cat "$REPO_ROOT/lanes/$lane.md")") \
    >"$log" 2>&1 || exit_code=$?

  # Truth test: did the lane record a completed lane_runs row today?
  local check_ok=1
  if [ -n "${TRADER_CHECK_CMD:-}" ]; then
    $TRADER_CHECK_CMD "$lane" >/dev/null 2>&1 && check_ok=0
  else
    (cd "$REPO_ROOT" && uv run trader lane check "$lane" >/dev/null 2>&1) && check_ok=0
  fi

  if [ "$check_ok" -ne 0 ]; then
    local tail_msg
    tail_msg="$(tail -c 300 "$log" 2>/dev/null | tr '\n' ' ')"
    fail "$lane" "no completed lane_runs row (claude exit $exit_code). Log: $log :: $tail_msg"
  fi
  if [ "$exit_code" -ne 0 ]; then
    # Lane completed in the DB but the process exited non-zero — warn, don't fail.
    bash "$NOTIFY" "agentic-trader: $lane lane warning" "completed in DB but claude exited $exit_code (log: $log)"
  fi
  echo "[run-lane] $lane completed (log: $log)"
}

case "$TARGET" in
  chain-premarket)
    for lane in research thesis risk; do
      run_one "$lane"   # fail() exits, aborting the chain
    done
    ;;
  *)
    for lane in "${LANES[@]}"; do
      if [ "$TARGET" = "$lane" ]; then
        run_one "$lane"
        exit 0
      fi
    done
    usage
    ;;
esac
