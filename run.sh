#!/bin/zsh
# Scheduled entry point. launchd fires missed runs on wake, so guard the
# time window: only trade 15:30-15:58 ET on weekdays.
set -euo pipefail
cd "$(dirname "$0")"
. scripts/timezone.sh
mkdir -p logs

log_runner() {
  echo "$(date '+%F %T') $*" >> logs/runner.log
}

agentic_trader_is_positive_integer() {
  case "${1:-}" in
    ""|*[!0-9]*|0) return 1 ;;
    *[1-9]*) return 0 ;;
    *) return 1 ;;
  esac
}

agentic_trader_is_nonnegative_integer() {
  case "${1:-}" in
    ""|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

agentic_trader_lock_helper() {
  python3 - "$@" <<'PY'
import errno
import os
import stat
import sys
import time
from datetime import datetime

LOCK_ARTIFACTS = ("pid", "started_epoch", "max_run_seconds")
LOCK_SUFFIX = ".lock"
REAPER_SUFFIX = ".reaper"


def log(message):
    with open("logs/runner.log", "a", encoding="utf-8") as fh:
        fh.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def fail(message):
    log(f"ERROR: {message}")
    print(f"ERROR: {message}", file=sys.stderr)
    return 2


def positive_int(value):
    if value is None or not value.isdigit():
        return None
    parsed = int(value, 10)
    if parsed <= 0:
        return None
    return parsed


def nonnegative_int(value):
    if value is None or not value.isdigit():
        return None
    return int(value, 10)


def validate_lock_dir(raw_path):
    if not raw_path:
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must not be empty")
    if "\x00" in raw_path:
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must not contain NUL bytes")
    if not os.path.isabs(raw_path):
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must be an absolute path")
    path = os.path.normpath(raw_path)
    basename = os.path.basename(path)
    if path == os.path.sep or basename in ("", ".", ".."):
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must not point at a root directory")
    if not basename.endswith(LOCK_SUFFIX):
        raise ValueError(f"AGENTIC_TRADER_LOCK_DIR basename must end with {LOCK_SUFFIX!r}")
    if os.path.islink(path):
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must not be a symlink")
    if os.path.exists(path) and not os.path.isdir(path):
        raise ValueError("AGENTIC_TRADER_LOCK_DIR must be a directory lock path")
    return path


def read_regular_file(path):
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return fh.readline().strip()


def read_lock_int(lock_dir, name, *, positive):
    value = read_regular_file(os.path.join(lock_dir, name))
    if positive:
        return positive_int(value)
    return nonnegative_int(value)


def pid_is_running(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_dir_age_seconds(lock_dir, now):
    try:
        st = os.stat(lock_dir, follow_symlinks=False)
    except FileNotFoundError:
        return 0
    return max(0, now - int(st.st_mtime))


def inspect_lock(lock_dir, fallback_max_run_seconds, pidless_grace_seconds):
    now = int(time.time())
    try:
        st = os.stat(lock_dir, follow_symlinks=False)
    except FileNotFoundError:
        return {"state": "missing"}
    if not stat.S_ISDIR(st.st_mode):
        return {"state": "error", "message": f"lock path {lock_dir!r} exists but is not a directory"}

    started_epoch = read_lock_int(lock_dir, "started_epoch", positive=False)
    if started_epoch is None:
        age_seconds = lock_dir_age_seconds(lock_dir, now)
    else:
        age_seconds = max(0, now - started_epoch)
    max_run_seconds = read_lock_int(lock_dir, "max_run_seconds", positive=True)
    if max_run_seconds is None:
        max_run_seconds = fallback_max_run_seconds

    owner_raw = read_regular_file(os.path.join(lock_dir, "pid"))
    owner = positive_int(owner_raw)
    if owner is None:
        if age_seconds < pidless_grace_seconds:
            return {
                "state": "fresh",
                "message": "skip: lock is being initialized (pid not written yet)",
            }
        if owner_raw:
            return {
                "state": "stale",
                "message": f"WARN: removing stale lock with invalid pid {owner_raw!r}",
            }
        return {"state": "stale", "message": "WARN: removing stale lock without pid"}

    if pid_is_running(owner):
        return {"state": "live", "owner": owner}

    return {
        "state": "stale",
        "message": f"WARN: removing stale lock held by dead pid {owner}",
    }


def remove_lock_artifacts(lock_dir):
    for name in LOCK_ARTIFACTS:
        path = os.path.join(lock_dir, name)
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            continue
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(errno.EISDIR, "lock artifact is a directory", path)
        os.unlink(path)
    os.rmdir(lock_dir)


def write_file(path, value):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"{value}\n")


def create_lock(lock_dir, owner_pid, max_run_seconds):
    os.mkdir(lock_dir, 0o700)
    os.chmod(lock_dir, 0o700)
    try:
        write_file(os.path.join(lock_dir, "started_epoch"), int(time.time()))
        write_file(os.path.join(lock_dir, "max_run_seconds"), max_run_seconds)
        write_file(os.path.join(lock_dir, "pid"), owner_pid)
    except Exception:
        try:
            remove_lock_artifacts(lock_dir)
        finally:
            raise


def acquire_reaper(reaper_dir, pidless_grace_seconds):
    while True:
        try:
            os.mkdir(reaper_dir, 0o700)
            os.chmod(reaper_dir, 0o700)
            return True
        except FileExistsError:
            age_seconds = lock_dir_age_seconds(reaper_dir, int(time.time()))
            if age_seconds < pidless_grace_seconds:
                log("skip: stale-lock reaper already active")
                return False
            try:
                os.rmdir(reaper_dir)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log(f"ERROR: stale-lock reaper path {reaper_dir!r} is not removable: {exc}")
                return False


def release_reaper(reaper_dir):
    try:
        os.rmdir(reaper_dir)
    except FileNotFoundError:
        return
    except OSError as exc:
        log(f"WARN: failed to remove stale-lock reaper {reaper_dir!r}: {exc}")


def acquire(lock_dir, owner_pid, max_run_seconds, pidless_grace_seconds):
    reaper_dir = f"{lock_dir}{REAPER_SUFFIX}"
    while True:
        try:
            create_lock(lock_dir, owner_pid, max_run_seconds)
            return 0
        except FileExistsError:
            pass
        except OSError as exc:
            return fail(f"failed to create lock {lock_dir!r}: {exc}")

        status = inspect_lock(lock_dir, max_run_seconds, pidless_grace_seconds)
        state = status["state"]
        if state == "missing":
            continue
        if state == "error":
            return fail(status["message"])
        if state == "live":
            log(f"skip: already running (pid {status['owner']})")
            return 1
        if state == "fresh":
            log(status["message"])
            return 1

        if not acquire_reaper(reaper_dir, pidless_grace_seconds):
            return 1
        try:
            # Re-read while holding the reaper mutex so a concurrent launcher cannot
            # cause us to clear a lock that stopped being stale after our first read.
            status = inspect_lock(lock_dir, max_run_seconds, pidless_grace_seconds)
            state = status["state"]
            if state == "missing":
                continue
            if state == "error":
                return fail(status["message"])
            if state == "live":
                log(f"skip: already running (pid {status['owner']})")
                return 1
            if state == "fresh":
                log(status["message"])
                return 1

            log(status["message"])
            try:
                remove_lock_artifacts(lock_dir)
            except FileNotFoundError:
                continue
            except OSError as exc:
                return fail(
                    f"refusing to remove stale lock {lock_dir!r}: "
                    f"only {', '.join(LOCK_ARTIFACTS)} may be removed ({exc})"
                )
        finally:
            release_reaper(reaper_dir)


def release(lock_dir, owner_pid):
    owner = read_lock_int(lock_dir, "pid", positive=True)
    if owner != owner_pid:
        return 0
    try:
        remove_lock_artifacts(lock_dir)
    except FileNotFoundError:
        return 0
    except OSError as exc:
        log(
            f"WARN: failed to remove lock {lock_dir!r}: "
            f"only {', '.join(LOCK_ARTIFACTS)} were eligible for removal ({exc})"
        )
    return 0


def main(argv):
    if not argv:
        return fail("lock helper missing mode")
    mode = argv[0]
    try:
        if mode == "acquire":
            if len(argv) != 5:
                return fail("lock helper acquire usage: acquire LOCK_DIR OWNER_PID MAX_RUN_SECONDS PIDLESS_GRACE_SECONDS")
            lock_dir = validate_lock_dir(argv[1])
            owner_pid = positive_int(argv[2])
            max_run_seconds = positive_int(argv[3])
            pidless_grace_seconds = nonnegative_int(argv[4])
            if owner_pid is None or max_run_seconds is None or pidless_grace_seconds is None:
                return fail("lock helper acquire received invalid numeric arguments")
            return acquire(lock_dir, owner_pid, max_run_seconds, pidless_grace_seconds)
        if mode == "release":
            if len(argv) != 3:
                return fail("lock helper release usage: release LOCK_DIR OWNER_PID")
            lock_dir = validate_lock_dir(argv[1])
            owner_pid = positive_int(argv[2])
            if owner_pid is None:
                return fail("lock helper release received invalid owner pid")
            return release(lock_dir, owner_pid)
        return fail(f"unknown lock helper mode {mode!r}")
    except ValueError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
PY
}

agentic_trader_acquire_lock() {
  agentic_trader_lock_helper acquire "$lock" "$$" "$LOCK_MAX_RUN_SECONDS" "$LOCK_PIDLESS_GRACE_SECONDS"
}

agentic_trader_release_lock() {
  agentic_trader_lock_helper release "$lock" "$$"
}

agentic_trader_timeout_wrapper_pid=""
agentic_trader_timeout_signal_name=""
agentic_trader_timeout_signal_status=""

agentic_trader_forward_timeout_signal() {
  agentic_trader_signal_name="$1"
  agentic_trader_signal_status="$2"
  agentic_trader_timeout_signal_name="$agentic_trader_signal_name"
  agentic_trader_timeout_signal_status="$agentic_trader_signal_status"
  trap '' HUP INT TERM
  if [ -n "${agentic_trader_timeout_wrapper_pid:-}" ]; then
    kill -"$agentic_trader_signal_name" "$agentic_trader_timeout_wrapper_pid" 2>/dev/null || true
  fi
}

agentic_trader_run_with_timeout() {
  timeout_seconds="$1"
  grace_seconds="$2"
  kill_wait_seconds="$3"
  shift 3

  agentic_trader_timeout_wrapper_pid=""
  agentic_trader_timeout_signal_name=""
  agentic_trader_timeout_signal_status=""
  trap 'agentic_trader_forward_timeout_signal HUP 129' HUP
  trap 'agentic_trader_forward_timeout_signal INT 130' INT
  trap 'agentic_trader_forward_timeout_signal TERM 143' TERM

  python3 - "$timeout_seconds" "$grace_seconds" "$kill_wait_seconds" "$@" <<'PY' &
import errno
import os
import signal
import subprocess
import sys
import time

HANDLED_TERMINATION_SIGNALS = (signal.SIGHUP, signal.SIGINT, signal.SIGTERM)


class ReceivedTerminationSignal(Exception):
    def __init__(self, signum: int):
        self.signum = signum
        super().__init__(signal_name(signum))


def signal_name(signum: int) -> str:
    try:
        return signal.Signals(signum).name
    except ValueError:
        return f"signal {signum}"


def signal_enum(signum: int) -> signal.Signals:
    return signal.Signals(signum)


def raise_termination_signal(signum: int, _frame) -> None:
    raise ReceivedTerminationSignal(signum)


def install_termination_signal_handlers() -> None:
    for sig in HANDLED_TERMINATION_SIGNALS:
        signal.signal(sig, raise_termination_signal)


def ignore_termination_signal_handlers() -> None:
    for sig in HANDLED_TERMINATION_SIGNALS:
        signal.signal(sig, signal.SIG_IGN)


def exit_from_returncode(returncode: int) -> int:
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def process_group_exists(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        return True
    return True


def process_group_is_active(proc: subprocess.Popen, pgid: int) -> bool:
    proc.poll()
    return proc.returncode is None or process_group_exists(pgid)


def signal_process(proc: subprocess.Popen, pgid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pgid, sig)
        return
    except ProcessLookupError:
        return
    except OSError as exc:
        print(
            f"WARN: failed to signal process group {pgid} with {sig.name}: {exc}; "
            "signaling child process only",
            file=sys.stderr,
            flush=True,
        )
    try:
        proc.send_signal(sig)
    except ProcessLookupError:
        return
    except OSError as exc:
        print(
            f"WARN: failed to signal child process {proc.pid} with {sig.name}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def wait_for_process_group_exit(
    proc: subprocess.Popen, pgid: int, timeout_seconds: int
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        proc.poll()
        if proc.returncode is not None and not process_group_exists(pgid):
            return True

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            proc.poll()
            return proc.returncode is not None and not process_group_exists(pgid)

        step = min(remaining, 0.05)
        if proc.returncode is None:
            try:
                proc.wait(timeout=step)
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(step)


def terminate_process_group(
    proc: subprocess.Popen,
    pgid: int,
    initial_signal: signal.Signals,
    grace_seconds: int,
    kill_wait_seconds: int,
    message: str,
) -> None:
    if not process_group_is_active(proc, pgid):
        return

    print(message, file=sys.stderr, flush=True)
    signal_process(proc, pgid, initial_signal)
    if not wait_for_process_group_exit(proc, pgid, grace_seconds):
        print(
            f"ERROR: command ignored {initial_signal.name} after {grace_seconds}s; "
            f"sending SIGKILL to process group {pgid}",
            file=sys.stderr,
            flush=True,
        )
        signal_process(proc, pgid, signal.SIGKILL)
        if not wait_for_process_group_exit(proc, pgid, kill_wait_seconds):
            print(
                f"WARN: command process group {pgid} still alive after SIGKILL",
                file=sys.stderr,
                flush=True,
            )


timeout_seconds = int(sys.argv[1])
grace_seconds = int(sys.argv[2])
kill_wait_seconds = int(sys.argv[3])
command = sys.argv[4:]
proc = None
pgid = None
cleanup_complete = False

try:
    install_termination_signal_handlers()
    try:
        proc = subprocess.Popen(command, start_new_session=True)
    except FileNotFoundError:
        print(f"{command[0]}: command not found", file=sys.stderr)
        sys.exit(127)
    except OSError as exc:
        print(f"failed to start {command[0]}: {exc}", file=sys.stderr)
        sys.exit(126)

    pgid = proc.pid
    try:
        rc = exit_from_returncode(proc.wait(timeout=timeout_seconds))
        cleanup_complete = True
        sys.exit(rc)
    except subprocess.TimeoutExpired:
        ignore_termination_signal_handlers()
        terminate_process_group(
            proc,
            pgid,
            signal.SIGTERM,
            grace_seconds,
            kill_wait_seconds,
            (
                f"ERROR: command timed out after {timeout_seconds}s; "
                f"terminating process group {pgid}"
            ),
        )
        cleanup_complete = True
        sys.exit(124)
except ReceivedTerminationSignal as exc:
    ignore_termination_signal_handlers()
    if proc is not None and pgid is not None:
        sig = signal_enum(exc.signum)
        terminate_process_group(
            proc,
            pgid,
            sig,
            grace_seconds,
            kill_wait_seconds,
            f"WARN: received {sig.name}; terminating process group {pgid}",
        )
        cleanup_complete = True
    sys.exit(128 + exc.signum)
finally:
    if proc is not None and pgid is not None and not cleanup_complete:
        ignore_termination_signal_handlers()
        terminate_process_group(
            proc,
            pgid,
            signal.SIGTERM,
            grace_seconds,
            kill_wait_seconds,
            f"WARN: wrapper exiting unexpectedly; terminating process group {pgid}",
        )
PY
  agentic_trader_timeout_wrapper_pid=$!
  if [ -n "$agentic_trader_timeout_signal_name" ]; then
    kill -"$agentic_trader_timeout_signal_name" "$agentic_trader_timeout_wrapper_pid" 2>/dev/null || true
  fi
  if wait "$agentic_trader_timeout_wrapper_pid"; then
    wrapper_rc=0
  else
    wrapper_rc=$?
  fi
  if [ -n "$agentic_trader_timeout_signal_status" ]; then
    wait "$agentic_trader_timeout_wrapper_pid" 2>/dev/null || true
    wrapper_rc="$agentic_trader_timeout_signal_status"
  fi
  agentic_trader_timeout_wrapper_pid=""
  agentic_trader_timeout_signal_name=""
  agentic_trader_timeout_signal_status=""
  trap - HUP INT TERM
  return "$wrapper_rc"
}

CLAUDE_TIMEOUT_SECONDS="${AGENTIC_TRADER_CLAUDE_TIMEOUT_SECONDS:-600}"
CLAUDE_TIMEOUT_GRACE_SECONDS="${AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS:-5}"
CLAUDE_TIMEOUT_KILL_WAIT_SECONDS=5
LOCK_PIDLESS_GRACE_SECONDS="${AGENTIC_TRADER_LOCK_PIDLESS_GRACE_SECONDS:-5}"
if ! agentic_trader_is_positive_integer "$CLAUDE_TIMEOUT_SECONDS"; then
  log_runner "ERROR: invalid AGENTIC_TRADER_CLAUDE_TIMEOUT_SECONDS '$CLAUDE_TIMEOUT_SECONDS'"
  exit 2
fi
if ! agentic_trader_is_nonnegative_integer "$CLAUDE_TIMEOUT_GRACE_SECONDS"; then
  log_runner "ERROR: invalid AGENTIC_TRADER_CLAUDE_TIMEOUT_GRACE_SECONDS '$CLAUDE_TIMEOUT_GRACE_SECONDS'"
  exit 2
fi
if ! agentic_trader_is_nonnegative_integer "$LOCK_PIDLESS_GRACE_SECONDS"; then
  log_runner "ERROR: invalid AGENTIC_TRADER_LOCK_PIDLESS_GRACE_SECONDS '$LOCK_PIDLESS_GRACE_SECONDS'"
  exit 2
fi
CLAUDE_TIMEOUT_SECONDS=$((10#$CLAUDE_TIMEOUT_SECONDS))
CLAUDE_TIMEOUT_GRACE_SECONDS=$((10#$CLAUDE_TIMEOUT_GRACE_SECONDS))
LOCK_PIDLESS_GRACE_SECONDS=$((10#$LOCK_PIDLESS_GRACE_SECONDS))

host_tz="$(agentic_trader_detect_host_timezone)"
if ! agentic_trader_is_eastern_timezone "$host_tz"; then
  reason="$(agentic_trader_timezone_requirement_reason)"
  log_runner "WARN: host-TZ mismatch: refusing run; detected host timezone '$host_tz'. $reason"
  exit 0
fi

export TZ=America/New_York
dow=$(date +%u) # 1=Mon
hm_raw=$(date +%H%M)
hm=$((10#$hm_raw))
if (( dow > 5 )) || (( hm < 1530 || hm > 1558 )); then
  log_runner "skip: outside trading window"
  exit 0
fi

hms_raw=$(date +%H%M%S)
hms=$((10#$hms_raw))
current_seconds=$(( (hms / 10000) * 3600 + ((hms / 100) % 100) * 60 + (hms % 100) ))
seconds_until_close=$((16 * 3600 - current_seconds))
if (( seconds_until_close < 1 )); then
  log_runner "skip: at/past market close"
  exit 0
fi
CLAUDE_TERMINATION_SECONDS=$((CLAUDE_TIMEOUT_GRACE_SECONDS + CLAUDE_TIMEOUT_KILL_WAIT_SECONDS))
if (( seconds_until_close <= CLAUDE_TERMINATION_SECONDS )); then
  log_runner "skip: too close to market close for timeout cleanup"
  exit 0
fi
MAX_TIMEOUT_BEFORE_CLOSE=$((seconds_until_close - CLAUDE_TERMINATION_SECONDS))
if (( CLAUDE_TIMEOUT_SECONDS > MAX_TIMEOUT_BEFORE_CLOSE )); then
  log_runner "WARN: capping claude timeout to ${MAX_TIMEOUT_BEFORE_CLOSE}s to leave ${CLAUDE_TERMINATION_SECONDS}s for cleanup before close"
  CLAUDE_TIMEOUT_SECONDS="$MAX_TIMEOUT_BEFORE_CLOSE"
fi
LOCK_MAX_RUN_SECONDS=$((CLAUDE_TIMEOUT_SECONDS + CLAUDE_TERMINATION_SECONDS))

# Holiday / half-day skip: the trade window (15:30-15:58) is after a 13:00
# early close, so half-days are no-ops too. Guard before the lock so a
# holiday run is a clean no-op.
if ! python3 scripts/market_calendar.py --is-trading-day "$(date +%F)"; then
  log_runner "skip: market holiday/half-day"
  exit 0
fi

lock="${AGENTIC_TRADER_LOCK_DIR:-/tmp/agentic-trader.lock}"
if agentic_trader_acquire_lock; then
  :
else
  lock_rc=$?
  if [ "$lock_rc" -eq 1 ]; then
    exit 0
  fi
  exit "$lock_rc"
fi
trap 'agentic_trader_release_lock' EXIT

echo "$(date '+%F %T') run start" >> logs/runner.log
set +e
agentic_trader_run_with_timeout "$CLAUDE_TIMEOUT_SECONDS" "$CLAUDE_TIMEOUT_GRACE_SECONDS" "$CLAUDE_TIMEOUT_KILL_WAIT_SECONDS" \
  claude -p "Read TRADER.md and execute the daily trading run exactly as written." \
  --permission-mode dontAsk \
  --max-turns 40 \
  >> logs/runner.log 2>&1
rc=$?
set -e
echo "$(date '+%F %T') run end (exit $rc)" >> logs/runner.log
exit "$rc"
