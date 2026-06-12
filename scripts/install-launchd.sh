#!/usr/bin/env bash
# Install the agentic-trader launchd agent for the current user.
#
# Usage: bash scripts/install-launchd.sh
#
# Copies com.example.agentic-trader.plist with this repo's real path
# substituted in, writes it to ~/Library/LaunchAgents/, and loads it.
# Idempotent: re-running unloads any existing copy first.
#
# Schedule: weekdays at 15:45 (machine-local time, intended as ET). Keep it
# at 15:45 — the strategy computes its signal at ~3:45pm ET using the live
# price as a provisional close, so the order can fill before the 4pm bell.
#
# Migration: if you previously installed this agent under a different label,
# remove it first: launchctl bootout "gui/$(id -u)/<old-label>" and delete
# the old plist from ~/Library/LaunchAgents/.
set -euo pipefail

LABEL="com.example.agentic-trader"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_DIR/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"

mkdir -p "$HOME/Library/LaunchAgents" "$REPO_DIR/logs"

# Substitute the placeholder repo path with the real one.
sed "s|/PATH/TO/agentic-trader|$REPO_DIR|g" "$TEMPLATE" > "$TARGET"

# Unload a previously loaded copy (ignore "not loaded"), then load.
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$TARGET"

echo "Installed and loaded $LABEL (weekdays 15:45) from $TARGET"
