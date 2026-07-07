#!/usr/bin/env bash
# lane-wrapper.sh — out-of-repo safety net for scheduled lane runs.
#
# Installed by ops/install.sh to ~/.local/bin/agentic-trader-lane with
# __DEPLOY_ROOT__ substituted. launchd invokes THIS instead of run-lane.sh
# directly, so that if the deploy worktree is broken or missing (the failure
# mode that killed the 2026-07-07 exec-open run: script gone, and notify.sh
# gone with it), something outside the repo can still raise an alarm.
#
# Notification is inline osascript on purpose: ops/notify.sh lives in the
# same tree we are guarding against.
set -u

DEPLOY_ROOT="__DEPLOY_ROOT__"
RUN_LANE="$DEPLOY_ROOT/ops/run-lane.sh"
LANE="${1:-}"

alarm() { # alarm <message>
  msg="$(printf '%s' "$1" | sed 's/"/\\"/g')"
  osascript -e "display notification \"$msg\" with title \"agentic-trader: scheduler BROKEN\"" || true
  echo "agentic-trader-lane: $1" >&2
}

if [ -z "$LANE" ]; then
  alarm "wrapper invoked without a lane argument"
  exit 1
fi

if [ ! -f "$RUN_LANE" ] || [ ! -x "$RUN_LANE" ]; then
  alarm "$RUN_LANE missing or not executable — lane '$LANE' NOT run. Fix: ops/deploy.sh in the dev repo."
  exit 1
fi

cd "$DEPLOY_ROOT" || { alarm "cannot cd to $DEPLOY_ROOT — lane '$LANE' NOT run"; exit 1; }
exec bash "$RUN_LANE" "$LANE"
