"""Tests for ops/run-lane.sh failure detection and notification.

The runner's success criterion is a completed lane_runs row, checked via
`trader lane check` — here stubbed with TRADER_CHECK_CMD. Claude itself is
stubbed with TRADER_CLAUDE_CMD, the DB ping with TRADER_PING_CMD, and
TRADER_TEST=1 turns ops/notify.sh into a print.
"""

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_LANE = REPO_ROOT / "ops" / "run-lane.sh"


def run_lane(target, *, claude="/usr/bin/true", check="/usr/bin/true", ping="/usr/bin/true", extra_env=None):
    env = os.environ.copy()
    env.update(
        TRADER_TEST="1",
        TRADER_CLAUDE_CMD=claude,
        TRADER_CHECK_CMD=check,
        TRADER_PING_CMD=ping,
    )
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", str(RUN_LANE), target],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


@pytest.mark.parametrize(
    "script",
    ["run-lane.sh", "notify.sh", "install.sh", "uninstall.sh"],
)
def test_scripts_parse(script):
    proc = subprocess.run(["bash", "-n", str(REPO_ROOT / "ops" / script)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_success_when_lane_run_completed():
    proc = run_lane("research")
    assert proc.returncode == 0, proc.stderr
    assert "research completed" in proc.stdout
    assert "NOTIFY" not in proc.stdout


def test_failure_when_no_completed_lane_run():
    """Exit 0 from claude is NOT trusted: missing completed row -> fail + notify."""
    proc = run_lane("research", check="/usr/bin/false")
    assert proc.returncode == 1
    assert "NOTIFY: agentic-trader: research lane FAILED" in proc.stdout
    assert "no completed lane_runs row" in proc.stderr


def test_warns_but_passes_when_claude_dies_after_completion():
    proc = run_lane("review", claude="/usr/bin/false", check="/usr/bin/true")
    assert proc.returncode == 0
    assert "NOTIFY: agentic-trader: review lane warning" in proc.stdout


def test_preflight_db_ping_failure(tmp_path):
    marker = tmp_path / "claude_ran"
    fake_claude = tmp_path / "claude.sh"
    fake_claude.write_text(f"#!/bin/bash\ntouch {marker}\n")
    fake_claude.chmod(0o755)

    proc = run_lane("execution", claude=str(fake_claude), ping="/usr/bin/false")
    assert proc.returncode == 1
    assert "database unreachable" in proc.stderr
    assert not marker.exists(), "claude must not be invoked when pre-flight fails"


def test_unknown_lane_rejected():
    proc = run_lane("yolo")
    assert proc.returncode == 2
    assert "usage" in proc.stderr


def test_chain_premarket_aborts_on_first_failure(tmp_path):
    invoked = tmp_path / "invocations"
    fake_claude = tmp_path / "claude.sh"
    # Record which lane prompt was passed (last arg contains the lane's mission line).
    fake_claude.write_text('#!/bin/bash\necho "run" >> ' + str(invoked) + "\n")
    fake_claude.chmod(0o755)

    proc = run_lane("chain-premarket", claude=str(fake_claude), check="/usr/bin/false")
    assert proc.returncode == 1
    assert invoked.read_text().count("run") == 1, "thesis/risk must not run after research fails"
    assert "NOTIFY: agentic-trader: research lane FAILED" in proc.stdout


def test_chain_premarket_runs_all_three(tmp_path):
    invoked = tmp_path / "invocations"
    fake_claude = tmp_path / "claude.sh"
    fake_claude.write_text('#!/bin/bash\necho "run" >> ' + str(invoked) + "\n")
    fake_claude.chmod(0o755)

    proc = run_lane("chain-premarket", claude=str(fake_claude))
    assert proc.returncode == 0, proc.stderr
    assert invoked.read_text().count("run") == 3
