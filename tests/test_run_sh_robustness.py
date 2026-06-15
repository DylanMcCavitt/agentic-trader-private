"""Focused robustness tests for run.sh."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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


def write_fake_date(
    bin_dir: Path, *, hm: str = "1545", hms: str | None = None, dow: str = "3"
) -> None:
    fake_date = bin_dir / "date"
    hms = hms or f"{hm}00"
    fake_date.write_text(
        "#!/usr/bin/env sh\n"
        "case \"$1\" in\n"
        f"  '+%u') printf '%s\\n' '{dow}' ;;\n"
        f"  '+%H%M') printf '%s\\n' '{hm}' ;;\n"
        f"  '+%H%M%S') printf '%s\\n' '{hms}' ;;\n"
        "  '+%F') printf '%s\\n' '2026-06-10' ;;\n"
        "  '+%F %T') printf '%s\\n' '2026-06-10 15:45:00' ;;\n"
        "  *) exec /bin/date \"$@\" ;;\n"
        "esac\n"
    )
    fake_date.chmod(0o755)


def write_fake_claude(bin_dir: Path) -> None:
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env sh\n"
        "printf 'fake claude invoked: %s\\n' \"$*\"\n"
        "if [ -n \"${FAKE_CLAUDE_SLEEP:-}\" ]; then\n"
        "  sleep \"$FAKE_CLAUDE_SLEEP\"\n"
        "fi\n"
        "exit \"${FAKE_CLAUDE_RC:-0}\"\n"
    )
    fake_claude.chmod(0o755)


def write_fake_claude_exits_on_term_with_stubborn_child(bin_dir: Path) -> None:
    fake_claude = bin_dir / "claude"
    fake_claude.write_text(
        "#!/usr/bin/env sh\n"
        "set -eu\n"
        ": \"${FAKE_CLAUDE_CHILD_PID_FILE:?}\"\n"
        ": \"${FAKE_CLAUDE_CHILD_SURVIVOR_FILE:?}\"\n"
        "printf 'fake claude invoked: %s\\n' \"$*\"\n"
        "(\n"
        "  trap '' TERM HUP INT\n"
        "  sleep \"${FAKE_CLAUDE_CHILD_SURVIVOR_DELAY:-3}\"\n"
        "  printf 'child survived initial signal\\n' > \"$FAKE_CLAUDE_CHILD_SURVIVOR_FILE\"\n"
        "  while :; do sleep 1; done\n"
        ") &\n"
        "printf '%s\\n' \"$!\" > \"$FAKE_CLAUDE_CHILD_PID_FILE\"\n"
        "trap 'exit 0' TERM HUP INT\n"
        "while :; do sleep 1; done\n"
    )
    fake_claude.chmod(0o755)


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_pid_if_running(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def runner_env(tmp_path: Path, bin_dir: Path, **overrides: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "AGENTIC_TRADER_HOST_TZ_OVERRIDE": "America/New_York",
            "AGENTIC_TRADER_LOCK_DIR": str(tmp_path / "agentic-trader.lock"),
            "PATH": f"{bin_dir}:{env['PATH']}",
        }
    )
    env.update(overrides)
    return env


@pytest.mark.parametrize("shell", RUN_SHELLS)
def test_run_sh_base10_hhmm_parse_skips_0900_without_shell_octal_error(
    tmp_path: Path, shell: str
):
    repo = copy_repo_subset(tmp_path, "run.sh", "scripts/timezone.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir, hm="0900", dow="3")

    result = subprocess.run(
        [shell, str(repo / "run.sh")],
        env=runner_env(tmp_path, bin_dir),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "skip: outside trading window" in runner_log
    assert "run start" not in runner_log


@pytest.mark.parametrize("shell", RUN_SHELLS)
def test_run_sh_logs_failed_claude_exit_and_releases_lock(tmp_path: Path, shell: str):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir, FAKE_CLAUDE_RC="42")

    result = subprocess.run(
        [shell, str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 42, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "run start" in runner_log
    assert "fake claude invoked:" in runner_log
    assert "run end (exit 42)" in runner_log
    assert not Path(env["AGENTIC_TRADER_LOCK_DIR"]).exists()


def test_run_sh_times_out_hung_claude_and_releases_lock(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(
        tmp_path,
        bin_dir,
        AGENTIC_TRADER_CLAUDE_TIMEOUT_SECONDS="1",
        AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS="0",
        FAKE_CLAUDE_SLEEP="10",
    )

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 124, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "ERROR: command timed out after 1s" in runner_log
    assert "run end (exit 124)" in runner_log
    assert not Path(env["AGENTIC_TRADER_LOCK_DIR"]).exists()


@pytest.mark.parametrize("shell", RUN_SHELLS)
@pytest.mark.parametrize(
    ("termination_signal", "expected_status", "signal_name"),
    [
        pytest.param(signal.SIGTERM, 143, "SIGTERM", id="sigterm"),
        pytest.param(signal.SIGHUP, 129, "SIGHUP", id="sighup"),
        pytest.param(signal.SIGINT, 130, "SIGINT", id="sigint"),
    ],
)
def test_run_sh_external_signal_cleans_up_claude_process_group_before_timeout(
    tmp_path: Path,
    shell: str,
    termination_signal: signal.Signals,
    expected_status: int,
    signal_name: str,
):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude_exits_on_term_with_stubborn_child(bin_dir)
    child_pid_file = tmp_path / "stubborn-child.pid"
    survivor_file = tmp_path / "stubborn-child-survived.txt"
    env = runner_env(
        tmp_path,
        bin_dir,
        AGENTIC_TRADER_CLAUDE_TIMEOUT_SECONDS="30",
        AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS="1",
        FAKE_CLAUDE_CHILD_PID_FILE=str(child_pid_file),
        FAKE_CLAUDE_CHILD_SURVIVOR_FILE=str(survivor_file),
        FAKE_CLAUDE_CHILD_SURVIVOR_DELAY="3",
    )
    child_pid: int | None = None
    proc = subprocess.Popen(
        [shell, str(repo / "run.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not child_pid_file.exists():
            if proc.poll() is not None:
                stdout, stderr = proc.communicate()
                pytest.fail(
                    f"run.sh exited before fake claude child started: "
                    f"rc={proc.returncode}, stdout={stdout!r}, stderr={stderr!r}"
                )
            time.sleep(0.05)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())

        proc.send_signal(termination_signal)
        stdout, stderr = proc.communicate(timeout=10)

        assert proc.returncode == expected_status, f"stdout={stdout!r}\nstderr={stderr!r}"

        deadline = time.monotonic() + 2.5
        while (
            time.monotonic() < deadline
            and pid_is_running(child_pid)
            and not survivor_file.exists()
        ):
            time.sleep(0.05)

        runner_log = (repo / "logs/runner.log").read_text()
        assert f"WARN: received {signal_name}; terminating process group" in runner_log
        assert f"ERROR: command ignored {signal_name} after 1s; sending SIGKILL" in runner_log
        assert f"run end (exit {expected_status})" in runner_log
        assert not survivor_file.exists()
        assert not pid_is_running(child_pid)
        assert not Path(env["AGENTIC_TRADER_LOCK_DIR"]).exists()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if child_pid is not None and pid_is_running(child_pid):
            kill_pid_if_running(child_pid)


def test_run_sh_sigkills_lingering_process_group_child_after_claude_exits_on_term(
    tmp_path: Path,
):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude_exits_on_term_with_stubborn_child(bin_dir)
    child_pid_file = tmp_path / "stubborn-child.pid"
    survivor_file = tmp_path / "stubborn-child-survived.txt"
    env = runner_env(
        tmp_path,
        bin_dir,
        AGENTIC_TRADER_CLAUDE_TIMEOUT_SECONDS="1",
        AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS="1",
        FAKE_CLAUDE_CHILD_PID_FILE=str(child_pid_file),
        FAKE_CLAUDE_CHILD_SURVIVOR_FILE=str(survivor_file),
        FAKE_CLAUDE_CHILD_SURVIVOR_DELAY="3",
    )
    child_pid: int | None = None

    try:
        result = subprocess.run(
            ["bash", str(repo / "run.sh")],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )

        assert result.returncode == 124, result.stderr
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())

        deadline = time.monotonic() + 2.5
        while (
            time.monotonic() < deadline
            and pid_is_running(child_pid)
            and not survivor_file.exists()
        ):
            time.sleep(0.05)

        runner_log = (repo / "logs/runner.log").read_text()
        assert "ERROR: command timed out after 1s" in runner_log
        assert "run end (exit 124)" in runner_log
        assert not survivor_file.exists()
        assert not pid_is_running(child_pid)
        assert not Path(env["AGENTIC_TRADER_LOCK_DIR"]).exists()
    finally:
        if child_pid is not None and pid_is_running(child_pid):
            kill_pid_if_running(child_pid)


def test_run_sh_removes_stale_pid_lock(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir)
    lock_dir = Path(env["AGENTIC_TRADER_LOCK_DIR"])
    lock_dir.mkdir()
    dead = subprocess.Popen(["sh", "-c", "exit 0"])
    dead.wait()
    (lock_dir / "pid").write_text(f"{dead.pid}\n")

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert f"WARN: removing stale lock held by dead pid {dead.pid}" in runner_log
    assert "run end (exit 0)" in runner_log
    assert not lock_dir.exists()


def test_run_sh_does_not_clear_fresh_pidless_lock(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir)
    lock_dir = Path(env["AGENTIC_TRADER_LOCK_DIR"])
    lock_dir.mkdir()

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "skip: lock is being initialized" in runner_log
    assert "run start" not in runner_log
    assert lock_dir.exists()


def test_run_sh_refuses_to_recursively_delete_lock_dir_with_unknown_files(
    tmp_path: Path,
):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir)
    lock_dir = Path(env["AGENTIC_TRADER_LOCK_DIR"])
    lock_dir.mkdir()
    dead = subprocess.Popen(["sh", "-c", "exit 0"])
    dead.wait()
    (lock_dir / "pid").write_text(f"{dead.pid}\n")
    sentinel = lock_dir / "do-not-delete.txt"
    sentinel.write_text("keep me\n")

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert sentinel.read_text() == "keep me\n"
    assert lock_dir.exists()
    runner_log = (repo / "logs/runner.log").read_text()
    assert "refusing to remove stale lock" in runner_log
    assert "run start" not in runner_log


def test_run_sh_skips_live_pid_lock_even_when_metadata_is_old(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir)
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir)
    lock_dir = Path(env["AGENTIC_TRADER_LOCK_DIR"])
    lock_dir.mkdir()
    live = subprocess.Popen(["sleep", "30"])
    try:
        (lock_dir / "pid").write_text(f"{live.pid}\n")
        (lock_dir / "started_epoch").write_text(f"{int(time.time()) - 120}\n")
        (lock_dir / "max_run_seconds").write_text("1\n")

        result = subprocess.run(
            ["bash", str(repo / "run.sh")],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        runner_log = (repo / "logs/runner.log").read_text()
        assert f"skip: already running (pid {live.pid})" in runner_log
        assert "run start" not in runner_log
        assert lock_dir.exists()
    finally:
        live.terminate()
        live.wait(timeout=5)


def test_run_sh_timeout_cap_leaves_grace_and_sigkill_wait_before_close(
    tmp_path: Path,
):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir, hm="1558", hms="155850")
    write_fake_claude(bin_dir)
    env = runner_env(tmp_path, bin_dir)

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "capping claude timeout to 60s to leave 10s for cleanup before close" in runner_log
    assert "run end (exit 0)" in runner_log


def test_run_sh_skips_when_too_close_for_timeout_cleanup(tmp_path: Path):
    repo = copy_repo_subset(
        tmp_path, "run.sh", "scripts/timezone.sh", "scripts/market_calendar.py"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_fake_date(bin_dir, hm="1558", hms="155850")
    write_fake_claude(bin_dir)
    env = runner_env(
        tmp_path,
        bin_dir,
        AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS="65",
    )

    result = subprocess.run(
        ["bash", str(repo / "run.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    runner_log = (repo / "logs/runner.log").read_text()
    assert "skip: too close to market close for timeout cleanup" in runner_log
    assert "run start" not in runner_log
