"""Tests for launchd host-timezone guardrails."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NON_ET_TZ = "America/Los_Angeles"
ZSH = shutil.which("zsh") or ("/bin/zsh" if Path("/bin/zsh").exists() else None)
RUN_SHELLS = [
    pytest.param("bash", id="bash"),
    pytest.param(
        ZSH or "zsh",
        marks=pytest.mark.skipif(ZSH is None, reason="zsh not available"),
        id="zsh",
    ),
]


def copy_repo_subset(tmp_path: Path, *relative_paths: str) -> Path:
    repo = tmp_path / "repo"
    for relative_path in relative_paths:
        source = ROOT / relative_path
        destination = repo / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return repo


@pytest.mark.parametrize("detected_tz", [NON_ET_TZ, "EST"])
def test_install_launchd_refuses_non_et_or_ambiguous_host_before_installing(
    tmp_path: Path, detected_tz: str
):
    repo = copy_repo_subset(
        tmp_path,
        "scripts/install-launchd.sh",
        "scripts/timezone.sh",
        "com.example.agentic-trader.plist",
    )
    home = tmp_path / "home"
    env = os.environ.copy()
    env.update({"AGENTIC_TRADER_HOST_TZ_OVERRIDE": detected_tz, "HOME": str(home)})

    result = subprocess.run(
        ["bash", str(repo / "scripts/install-launchd.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing to install com.example.agentic-trader" in result.stderr
    assert f"Detected host timezone: {detected_tz}" in result.stderr
    assert "machine-local timezone" in result.stderr
    assert "Eastern Time" in result.stderr
    assert not (home / "Library/LaunchAgents/com.example.agentic-trader.plist").exists()


def test_install_launchd_allows_et_host_and_bootstraps(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path,
        "scripts/install-launchd.sh",
        "scripts/timezone.sh",
        "com.example.agentic-trader.plist",
    )
    home = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launchctl_log = tmp_path / "launchctl.log"
    fake_launchctl = bin_dir / "launchctl"
    fake_launchctl.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$LAUNCHCTL_LOG\"\n"
    )
    fake_launchctl.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "AGENTIC_TRADER_HOST_TZ_OVERRIDE": "America/New_York",
            "HOME": str(home),
            "LAUNCHCTL_LOG": str(launchctl_log),
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )

    result = subprocess.run(
        ["bash", str(repo / "scripts/install-launchd.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    target = home / "Library/LaunchAgents/com.example.agentic-trader.plist"
    assert target.exists()
    assert str(repo) in target.read_text()
    launchctl_calls = launchctl_log.read_text()
    assert "bootout gui/" in launchctl_calls
    assert "bootstrap gui/" in launchctl_calls
    assert str(target) in launchctl_calls


@pytest.mark.parametrize("shell", RUN_SHELLS)
def test_run_sh_logs_distinct_warn_for_non_et_host(tmp_path: Path, shell: str):
    repo = copy_repo_subset(tmp_path, "run.sh", "scripts/timezone.sh")
    env = os.environ.copy()
    env["AGENTIC_TRADER_HOST_TZ_OVERRIDE"] = NON_ET_TZ

    result = subprocess.run(
        [shell, str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "WARN: host-TZ mismatch" in runner_log
    assert NON_ET_TZ in runner_log
    assert "StartCalendarInterval" in runner_log
    assert "skip: outside trading window" not in runner_log
    assert "run start" not in runner_log
