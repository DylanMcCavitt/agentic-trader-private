#!/usr/bin/env bash
# deploy.sh — create/refresh the deployment worktree the scheduler runs from.
#
# Scheduled launchd jobs must NOT execute from the dev checkout: a background
# git operation there (branch switch, rebase) can make ops/run-lane.sh — and
# with it ops/notify.sh — vanish mid-schedule, so runs die silently. Instead
# they run from a stable worktree at ~/src/agentic-trader-deploy pinned to
# master. This script is the "ship a new version to the scheduler" command:
#
#   1. create the worktree if missing, else fast-forward it to current master
#   2. uv sync the deploy environment
#   3. copy config.local.json (gitignored, needed at runtime)
#   4. ensure logs/ and state/ dirs exist
#
# The worktree is checked out DETACHED at master's commit (git refuses to
# check out a branch already checked out in the dev repo). Journal writes
# (`trader journal write`) land in the deploy worktree's journal/ — they are
# regenerable from the DB, and a refresh discards uncommitted journal edits
# (see docs/runbook.md).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_ROOT="${TRADER_DEPLOY_ROOT:-$HOME/src/agentic-trader-deploy}"

echo "[deploy] dev repo:        $REPO_ROOT"
echo "[deploy] deploy worktree: $DEPLOY_ROOT"

# --- 1. create or refresh the worktree --------------------------------------
git -C "$REPO_ROOT" worktree prune
if [ ! -e "$DEPLOY_ROOT/.git" ]; then
  git -C "$REPO_ROOT" worktree add --detach "$DEPLOY_ROOT" master
  echo "[deploy] created worktree at $(git -C "$DEPLOY_ROOT" rev-parse --short HEAD)"
else
  dirty="$(git -C "$DEPLOY_ROOT" status --porcelain)"
  if [ -n "$dirty" ]; then
    echo "[deploy] discarding local tracked changes in deploy worktree (journal etc. are DB-regenerable):"
    echo "$dirty" | sed 's/^/[deploy]   /'
  fi
  # --force discards local tracked changes; untracked files (config.local.json,
  # logs/, state/) are left alone.
  git -C "$DEPLOY_ROOT" checkout --force --detach master
  echo "[deploy] refreshed to $(git -C "$DEPLOY_ROOT" rev-parse --short HEAD)"
fi

# --- 2. dependencies ---------------------------------------------------------
(cd "$DEPLOY_ROOT" && uv sync)

# --- 3. local config (gitignored, runtime-required) ---------------------------
if [ -f "$REPO_ROOT/config.local.json" ]; then
  cp "$REPO_ROOT/config.local.json" "$DEPLOY_ROOT/config.local.json"
  echo "[deploy] copied config.local.json"
else
  echo "[deploy] WARNING: no config.local.json in dev repo; deploy worktree has none" >&2
fi

# --- 4. runtime dirs ----------------------------------------------------------
mkdir -p "$DEPLOY_ROOT/logs/lanes" "$DEPLOY_ROOT/logs/launchd" "$DEPLOY_ROOT/state"

echo "[deploy] done. Scheduler runs from: $DEPLOY_ROOT"
