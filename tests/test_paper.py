"""Tests for scripts/paper.py — fill math, marking, and stats."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import paper  # noqa: E402

TODAY = "2026-06-12"
LATER = "2026-06-15"


def book(cash=10000.0):
    return paper.new_book(cash, TODAY)


# --- equity -----------------------------------------------------------------

def test_equity_round_trip_no_slippage():
    b = book()
    paper.open_equity(b, "SPY", 100.0, 0.0, 1.0, TODAY)
    assert b["cash"] == pytest.approx(0.0)
    assert b["position"]["shares"] == pytest.approx(100.0)
    paper.close_equity(b, 110.0, 0.0, LATER, "exit")
    assert b["position"] is None
    assert b["cash"] == pytest.approx(11000.0)
    trade = b["trades"][0]
    assert trade["ret"] == pytest.approx(0.10)
    assert trade["pnl"] == pytest.approx(1000.0)


def test_equity_slippage_hits_both_sides():
    b = book()
    paper.open_equity(b, "SPY", 100.0, 100.0, 1.0, TODAY)  # 100 bps = 1%
    assert b["position"]["entry_price"] == pytest.approx(101.0)
    paper.close_equity(b, 100.0, 100.0, LATER, "exit")
    # bought at 101, sold at 99 -> ~-1.98%
    assert b["trades"][0]["ret"] == pytest.approx(99 / 101 - 1)


def test_equity_position_fraction_leaves_cash():
    b = book()
    paper.open_equity(b, "SPY", 100.0, 0.0, 0.5, TODAY)
    assert b["cash"] == pytest.approx(5000.0)
    assert b["position"]["shares"] == pytest.approx(50.0)


# --- options ----------------------------------------------------------------

CONTRACT = {"underlying": "QQQ", "right": "call", "strike": 500.0,
            "expiry": "2026-07-17", "fill": 20.0}


def test_option_open_sizes_by_alloc():
    b = book()  # 10k * 0.35 = 3500 / 2000-per-contract -> 1 contract
    detail = paper.open_option(b, CONTRACT, 0.35, TODAY)
    assert "1x QQQ" in detail
    assert b["cash"] == pytest.approx(8000.0)
    assert b["position"]["contracts"] == 1


def test_option_open_buys_one_if_alloc_too_small_but_affordable():
    b = book(2500.0)  # alloc 0.35 -> 875 < one contract (2000), but affordable
    paper.open_option(b, CONTRACT, 0.35, TODAY)
    assert b["position"]["contracts"] == 1
    assert b["cash"] == pytest.approx(500.0)


def test_option_open_skips_when_unaffordable():
    b = book(1500.0)
    assert paper.open_option(b, CONTRACT, 0.35, TODAY) is None
    assert b["position"] is None
    assert b["cash"] == pytest.approx(1500.0)


def test_option_close_books_pnl():
    b = book()
    paper.open_option(b, CONTRACT, 0.35, TODAY)
    paper.close_option(b, 30.0, LATER, "exit signal")
    assert b["position"] is None
    assert b["cash"] == pytest.approx(11000.0)  # 8000 + 3000
    assert b["trades"][0]["ret"] == pytest.approx(0.5)
    assert b["trades"][0]["pnl"] == pytest.approx(1000.0)


def test_option_close_at_zero_books_total_loss():
    b = book()
    paper.open_option(b, CONTRACT, 0.35, TODAY)
    paper.close_option(b, 0.0, LATER, "expired, intrinsic")
    assert b["cash"] == pytest.approx(8000.0)
    assert b["trades"][0]["ret"] == pytest.approx(-1.0)


def test_option_fee_affects_sizing_cash_pnl_and_ret():
    c = {**CONTRACT, "fill": 10.0}
    b = book(3000.0)
    paper.open_option(b, c, 1.0, TODAY, fee_per_contract=1.0)
    assert b["position"]["contracts"] == 2  # 3 x (1000 + fee) is unaffordable
    assert b["cash"] == pytest.approx(998.0)
    paper.close_option(b, 12.0, LATER, "exit signal", fee_per_contract=1.0)
    assert b["cash"] == pytest.approx(3396.0)  # 998 + 2 * (1200 - 1)
    trade = b["trades"][0]
    assert trade["pnl"] == pytest.approx(396.0)
    assert trade["ret"] == pytest.approx(round(2398 / 2002 - 1, 6))


# --- mark + stats -----------------------------------------------------------

def test_mark_is_idempotent_per_day():
    b = book()
    paper.mark(b, TODAY)
    paper.mark(b, TODAY)
    assert len(b["history"]) == 1
    paper.mark(b, LATER)
    assert len(b["history"]) == 2


def test_mark_values_open_positions():
    b = book()
    paper.open_equity(b, "SPY", 100.0, 0.0, 1.0, TODAY)
    assert paper.mark(b, TODAY, equity_price=105.0) == pytest.approx(10500.0)
    b2 = book()
    paper.open_option(b2, CONTRACT, 0.35, TODAY)
    assert paper.mark(b2, TODAY, option_premium=25.0) == pytest.approx(10500.0)


def test_mark_without_quote_falls_back_to_entry():
    b = book()
    paper.open_equity(b, "SPY", 100.0, 0.0, 1.0, TODAY)
    assert paper.mark(b, TODAY) == pytest.approx(10000.0)


def test_stats_drawdown_and_win_rate():
    b = book()
    b["history"] = [{"date": "d1", "value": 10000.0},
                    {"date": "d2", "value": 12000.0},
                    {"date": "d3", "value": 9000.0},
                    {"date": "d4", "value": 11000.0}]
    b["trades"] = [{"pnl": 500.0}, {"pnl": -200.0}, {"pnl": 100.0}]
    s = paper.stats(b)
    assert s["value"] == pytest.approx(11000.0)
    assert s["total_return"] == pytest.approx(0.10)
    assert s["max_drawdown"] == pytest.approx(9000 / 12000 - 1)
    assert s["trades"] == 3
    assert s["win_rate"] == pytest.approx(2 / 3, abs=1e-3)


def test_stats_empty_book():
    s = paper.stats(book())
    assert s["total_return"] == 0.0
    assert s["win_rate"] is None
