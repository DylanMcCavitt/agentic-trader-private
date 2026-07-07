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


@pytest.fixture(autouse=True)
def _isolated_log_dir(tmp_path, monkeypatch):
    """Point TRADER_LOG_DIR at a tmp dir so pytest never writes into the
    real logs/lanes/ (stray empty logs there misled real-incident triage)."""
    monkeypatch.setenv("TRADER_LOG_DIR", str(tmp_path / "lane-logs"))


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
    ["run-lane.sh", "notify.sh", "install.sh", "uninstall.sh", "deploy.sh", "lane-wrapper.sh"],
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


def test_logs_written_to_trader_log_dir_not_repo(tmp_path):
    """TRADER_LOG_DIR must fully redirect lane logs away from logs/lanes/."""
    log_dir = tmp_path / "isolated-logs"
    before = set((REPO_ROOT / "logs" / "lanes").glob("*")) if (REPO_ROOT / "logs" / "lanes").exists() else set()

    proc = run_lane("research", extra_env={"TRADER_LOG_DIR": str(log_dir)})
    assert proc.returncode == 0, proc.stderr

    after = set((REPO_ROOT / "logs" / "lanes").glob("*")) if (REPO_ROOT / "logs" / "lanes").exists() else set()
    assert after == before, "run-lane.sh wrote into the real logs/lanes/ despite TRADER_LOG_DIR"
    assert list(log_dir.glob("research-*.log")), "no log written to TRADER_LOG_DIR"


def _render_wrapper(tmp_path, deploy_root):
    """Render lane-wrapper.sh the way install.sh does (substitute deploy root)."""
    template = (REPO_ROOT / "ops" / "lane-wrapper.sh").read_text()
    wrapper = tmp_path / "agentic-trader-lane"
    wrapper.write_text(template.replace("__DEPLOY_ROOT__", str(deploy_root)))
    wrapper.chmod(0o755)
    return wrapper


def test_wrapper_alarms_when_run_lane_missing(tmp_path):
    """Broken/missing deploy worktree -> wrapper exits 1 without invoking anything.

    osascript is stubbed with a PATH shim so the test never fires a real
    notification but still proves the alarm path is exercised.
    """
    deploy_root = tmp_path / "deploy"  # does not exist
    wrapper = _render_wrapper(tmp_path, deploy_root)

    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "osascript").write_text('#!/bin/bash\necho "OSASCRIPT: $*"\n')
    (bindir / "osascript").chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env['PATH']}"
    proc = subprocess.run(["bash", str(wrapper), "execution"], capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert "OSASCRIPT" in proc.stdout
    assert "NOT run" in proc.stderr


def test_wrapper_execs_run_lane_when_present(tmp_path):
    deploy_root = tmp_path / "deploy"
    (deploy_root / "ops").mkdir(parents=True)
    fake_run_lane = deploy_root / "ops" / "run-lane.sh"
    fake_run_lane.write_text('#!/bin/bash\necho "ran lane: $1 from $(pwd)"\n')
    fake_run_lane.chmod(0o755)
    wrapper = _render_wrapper(tmp_path, deploy_root)

    proc = subprocess.run(["bash", str(wrapper), "review"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert f"ran lane: review from {deploy_root}" in proc.stdout
