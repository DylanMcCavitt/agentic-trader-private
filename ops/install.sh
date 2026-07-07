#!/usr/bin/env bash
# install.sh — install the agentic-trader launchd schedules.
#
# Scheduled runs execute from a stable deployment worktree
# (~/src/agentic-trader-deploy, refreshed by ops/deploy.sh), NOT this dev
# checkout — a branch switch in the dev tree must never break the scheduler.
# launchd invokes the out-of-repo wrapper ~/.local/bin/agentic-trader-lane,
# which alarms via osascript if the deploy worktree is broken.
#
# launchd StartCalendarInterval fires in the MACHINE'S local timezone. The
# plists encode ET wall-clock times, so this refuses to install unless the
# machine timezone is America/New_York (override with --force at your own
# risk — the schedules will fire at the wrong market times).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/launchd"
DEST="$HOME/Library/LaunchAgents"
DEPLOY_ROOT="${TRADER_DEPLOY_ROOT:-$HOME/src/agentic-trader-deploy}"
WRAPPER="$HOME/.local/bin/agentic-trader-lane"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

# --- timezone check --------------------------------------------------------
tz="$(readlink /etc/localtime 2>/dev/null | sed 's|.*zoneinfo/||' || true)"
if [ "$tz" != "America/New_York" ]; then
  echo "Machine timezone is '$tz', not America/New_York." >&2
  echo "launchd schedules are machine-local; they would fire at the wrong market times." >&2
  if [ "$FORCE" -ne 1 ]; then
    echo "Refusing to install. Fix with: sudo systemsetup -settimezone America/New_York" >&2
    echo "Or re-run with --force if you really know what you're doing." >&2
    exit 1
  fi
  echo "--force given; installing anyway." >&2
fi

# --- prerequisites ---------------------------------------------------------
command -v uv >/dev/null 2>&1 || { echo "uv not on PATH" >&2; exit 1; }
command -v claude >/dev/null 2>&1 || { echo "claude not on PATH" >&2; exit 1; }
mkdir -p "$DEST"

# --- deployment worktree ---------------------------------------------------
bash "$SCRIPT_DIR/deploy.sh"

# --- out-of-repo wrapper ---------------------------------------------------
mkdir -p "$(dirname "$WRAPPER")"
sed "s|__DEPLOY_ROOT__|$DEPLOY_ROOT|g" "$SCRIPT_DIR/lane-wrapper.sh" > "$WRAPPER"
chmod 755 "$WRAPPER"
echo "installed wrapper $WRAPPER"

# --- install schedules -----------------------------------------------------
uid="$(id -u)"
for src in "$PLIST_SRC"/com.agentic-trader.*.plist; do
  name="$(basename "$src")"
  label="${name%.plist}"
  dst="$DEST/$name"
  sed -e "s|__WRAPPER__|$WRAPPER|g" -e "s|__DEPLOY_ROOT__|$DEPLOY_ROOT|g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null

  # Re-bootstrap cleanly if already loaded.
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$uid" "$dst"
  echo "installed $label"
done

echo
echo "All schedules installed. Scheduler runs from: $DEPLOY_ROOT"
echo "Verify with:"
echo "  launchctl list | grep agentic-trader"
echo "Ship a new version to the scheduler with: ops/deploy.sh"
