#!/usr/bin/env bash
# install.sh — install the agentic-trader launchd schedules.
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
mkdir -p "$REPO_ROOT/logs/launchd" "$DEST"

# --- install ---------------------------------------------------------------
uid="$(id -u)"
for src in "$PLIST_SRC"/com.agentic-trader.*.plist; do
  name="$(basename "$src")"
  label="${name%.plist}"
  dst="$DEST/$name"
  sed "s|__REPO_ROOT__|$REPO_ROOT|g" "$src" > "$dst"
  plutil -lint "$dst" >/dev/null

  # Re-bootstrap cleanly if already loaded.
  launchctl bootout "gui/$uid/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$uid" "$dst"
  echo "installed $label"
done

echo
echo "All schedules installed. Verify with:"
echo "  launchctl list | grep agentic-trader"
