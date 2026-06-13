"""Tests for scripts/market_calendar.py — holiday/half-day calendar + CLI.

The pure functions are imported directly; the CLI contract that run.sh relies
on is exercised as a subprocess (the way run.sh invokes it), asserting on the
exit code.
"""
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from market_calendar import (  # noqa: E402
    early_close_minutes,
    is_market_holiday,
    is_trading_day,
)

CLI = SCRIPTS / "market_calendar.py"


def cli(*args):
    return subprocess.run([sys.executable, str(CLI), *args],
                          capture_output=True, text=True)


# --- is_market_holiday ---------------------------------------------------
@pytest.mark.parametrize("d", [
    "2026-01-01", "2026-06-19", "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-06-18", "2027-12-24",
])
def test_holiday_true(d):
    assert is_market_holiday(d) is True
    assert is_market_holiday(date.fromisoformat(d)) is True


@pytest.mark.parametrize("d", ["2026-06-10", "2026-12-31", "2027-03-25"])
def test_normal_day_not_holiday(d):
    assert is_market_holiday(d) is False


# --- early_close_minutes -------------------------------------------------
@pytest.mark.parametrize("d", ["2026-11-27", "2026-12-24",
                               "2027-11-26", "2027-12-23"])
def test_early_close_half_day(d):
    assert early_close_minutes(d) == 780  # 13:00 ET
    assert early_close_minutes(date.fromisoformat(d)) == 780


@pytest.mark.parametrize("d", ["2026-06-10", "2026-11-26", "2026-12-25"])
def test_early_close_none_on_full_and_holiday(d):
    assert early_close_minutes(d) is None


# --- is_trading_day ------------------------------------------------------
def test_is_trading_day_normal_weekday():
    assert is_trading_day("2026-06-10") is True  # Wednesday


@pytest.mark.parametrize("d", [
    "2026-06-13",  # Saturday
    "2026-06-14",  # Sunday
    "2026-06-19",  # holiday (Juneteenth)
    "2026-11-27",  # early-close half-day
])
def test_is_trading_day_false(d):
    assert is_trading_day(d) is False


# --- CLI: --is-trading-day (run.sh contract) -----------------------------
def test_cli_trading_day_exit0():
    assert cli("--is-trading-day", "2026-06-10").returncode == 0  # normal Wed


@pytest.mark.parametrize("d", [
    "2026-06-13",  # Saturday
    "2026-06-19",  # holiday
    "2026-11-27",  # half-day
])
def test_cli_trading_day_nonzero(d):
    assert cli("--is-trading-day", d).returncode != 0


# --- CLI: --is-holiday ---------------------------------------------------
def test_cli_is_holiday():
    assert cli("--is-holiday", "2026-06-19").returncode == 0
    assert cli("--is-holiday", "2026-06-10").returncode != 0


# --- CLI: bad input ------------------------------------------------------
def test_cli_bad_date_exits_nonzero():
    assert cli("--is-trading-day", "not-a-date").returncode == 2


def test_cli_unknown_flag_exits_nonzero():
    assert cli("--nonsense", "2026-06-10").returncode == 2
