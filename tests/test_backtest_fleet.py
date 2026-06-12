"""Tests for the fleet backtester layers.

- scalar/series signal equivalence: the live engine and the backtester must
  see the same entries/exits (one source of truth).
- Black-Scholes sanity: bounds, parity, degenerate inputs.
- deterministic mini-backtests on hand-built frames (no network).
"""
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import backtest_fleet  # noqa: E402
from strategies import SIGNAL_SERIES, SIGNALS, signals  # noqa: E402
from strategies.pricing import bs_price  # noqa: E402


def make_df(closes, highs=None, lows=None):
    idx = pd.bdate_range(end=pd.Timestamp("2026-06-12"), periods=len(closes))
    closes = [float(c) for c in closes]
    return pd.DataFrame({
        "Open": closes,
        "High": [float(h) for h in highs] if highs else closes,
        "Low": [float(l) for l in lows] if lows else closes,
        "Close": closes,
        "Volume": [0] * len(closes),
    }, index=idx)


# --- scalar/series equivalence ----------------------------------------------

PARAMS = {
    "rsi2_long": {"sma_trend": 5, "sma_exit": 3, "entry_rsi": 60.0},
    "rsi2_short": {"sma_trend": 15, "entry_rsi": 80.0, "exit_rsi": 30.0},
    "ibs_long": {"sma_trend": 5, "sma_exit": 3, "entry_ibs": 0.2, "exit_ibs": 0.8},
    "bollinger_long": {"sma_trend": 10, "bb_len": 5, "bb_std": 2.0},
    "donchian_long": {"entry_high": 5, "exit_low": 3},
    "donchian_short": {"entry_low": 5, "exit_high": 3, "sma_trend": 5},
}

# A frame with ups, downs, ranges, and a breakout — exercises every signal.
WIGGLY = make_df(
    [100, 102, 99, 104, 101, 107, 103, 110, 96, 94, 105, 112, 90, 95, 116, 120],
    highs=[101, 103, 101, 105, 103, 108, 105, 112, 99, 96, 107, 114, 93, 97, 118, 122],
    lows=[99, 100, 97, 102, 99, 105, 101, 108, 94, 92, 103, 110, 88, 92, 113, 118],
)


@pytest.mark.parametrize("name", sorted(SIGNALS))
def test_scalar_matches_series_last_row(name):
    p = PARAMS[name]
    for cut in (8, 12, len(WIGGLY)):  # several "today"s along the frame
        df = WIGGLY.iloc[:cut]
        scalar = SIGNALS[name](df, p)
        series = SIGNAL_SERIES[name](df, p)
        assert scalar["entry"] == bool(series["entry"].iloc[-1]), (name, cut)
        assert scalar["exit"] == bool(series["exit"].iloc[-1]), (name, cut)


def test_registries_cover_same_signals():
    assert set(SIGNALS) == set(SIGNAL_SERIES)


def test_rotation_targets_matches_scalar():
    dfs = {"SPY": make_df([100, 100, 100, 110]),
           "QQQ": make_df([100, 100, 100, 120])}
    series = signals.rotation_targets(dfs, {"lookback": 3})
    scalar = signals.momentum_rotation(dfs, {"lookback": 3})
    assert series.iloc[-1] == scalar["target"] == "QQQ"


def test_rotation_targets_cash_when_negative():
    dfs = {"SPY": make_df([100, 100, 100, 90]),
           "QQQ": make_df([100, 100, 100, 80])}
    assert signals.rotation_targets(dfs, {"lookback": 3}).iloc[-1] is None


# --- Black-Scholes sanity -----------------------------------------------------

def test_bs_call_bounds_and_monotonicity():
    px = bs_price(100, 95, 30 / 365.25, 0.2, 0.04, "call")
    assert px > 5.0  # above intrinsic
    assert px < 100  # below spot
    assert px > bs_price(100, 95, 10 / 365.25, 0.2, 0.04, "call")  # theta
    assert bs_price(100, 95, 30 / 365.25, 0.4, 0.04, "call") > px  # vega


def test_bs_put_call_parity():
    s, k, t, sig, r = 100.0, 105.0, 60 / 365.25, 0.25, 0.04
    call = bs_price(s, k, t, sig, r, "call")
    put = bs_price(s, k, t, sig, r, "put")
    assert call - put == pytest.approx(s - k * math.exp(-r * t), abs=1e-9)


def test_bs_degenerate_inputs_are_intrinsic():
    assert bs_price(100, 90, 0.0, 0.2, 0.04, "call") == pytest.approx(10.0)
    assert bs_price(100, 110, 0.0, 0.2, 0.04, "call") == 0.0
    assert bs_price(100, 110, 0.1, 0.0, 0.0, "put") == pytest.approx(10.0)


def test_realized_vol_positive_and_finite():
    vol = backtest_fleet.realized_vol(WIGGLY["Close"])
    tail = vol.dropna()
    assert (tail > 0).all() and (tail < 5).all()


# --- deterministic mini-backtests ---------------------------------------------

PCFG = {"starting_cash": 10000, "position_fraction": 1.0, "slippage_bps": 0.0,
        "option_alloc": 0.35, "option_spread_take": 0.25}


def test_backtest_equity_donchian_round_trip():
    # Flat 105s, breakout to 111+, then a breakdown through the channel low:
    # exactly one full trade.
    closes = [105] * 24 + [111, 112, 113, 95, 95]
    highs = [110] * 24 + [112, 113, 114, 96, 96]
    lows = [100] * 24 + [110, 111, 112, 94, 94]
    df = make_df(closes, highs=highs, lows=lows)
    spec = {"kind": "equity", "symbol": "T", "signal": "donchian_long",
            "params": {"entry_high": 20, "exit_low": 10}}
    book = backtest_fleet.backtest_equity(spec, df, PCFG, df.index[0])
    assert len(book["trades"]) == 1
    trade = book["trades"][0]
    assert trade["ret"] == pytest.approx(95 / 111 - 1)  # in at 111, out at 95
    assert book["position"] is None
    stats = backtest_fleet.perf(book)
    assert stats["final"] == pytest.approx(10000 * 95 / 111, rel=1e-6)


def test_backtest_equity_warmup_respects_start():
    closes = [105] * 24 + [111, 112, 113, 95, 95]
    df = make_df(closes)
    spec = {"kind": "equity", "symbol": "T", "signal": "donchian_long",
            "params": {"entry_high": 20, "exit_low": 10}}
    # Start after the breakout bar: the entry must not happen.
    start = df.index[-2]
    book = backtest_fleet.backtest_equity(spec, df, PCFG, start)
    assert book["trades"] == [] and book["position"] is None
    assert book["history"][0]["date"] == str(start.date())


def test_backtest_option_dte_stop_closes_position():
    # Breakout on the first tradable day opens a call; no exit signal after,
    # so only the DTE stop can close it. dte_mid = 9 -> stop at <= 7 DTE
    # fires two trading days later.
    closes = [105] * 24 + [111] * 6
    highs = [110] * 24 + [112] * 6
    lows = [100] * 24 + [110] * 6
    df = make_df(closes, highs=highs, lows=lows)
    spec = {"kind": "option", "symbol": "T", "right": "call",
            "signal": "donchian_long",
            "params": {"entry_high": 20, "exit_low": 10, "itm_pct": 0.05,
                       "dte_min": 8, "dte_max": 10, "exit_dte": 7}}
    book = backtest_fleet.backtest_option(spec, df, PCFG, df.index[24],
                                          iv_premium=1.15, opt_slip=0.015,
                                          r=0.04)
    assert len(book["trades"]) == 1
    assert "dte stop" in book["trades"][0]["detail"]
    assert book["position"] is None


def test_backtest_option_flat_vol_means_no_entry():
    # Donchian entry fires (close above the prior channel high) but the
    # closes are identical -> realized vol 0 -> the sg > 0 guard skips entry.
    flat = make_df([105] * 27, highs=[104] * 27, lows=[90] * 27)
    spec = {"kind": "option", "symbol": "T", "right": "call",
            "signal": "donchian_long",
            "params": {"entry_high": 20, "exit_low": 10, "itm_pct": 0.05,
                       "dte_min": 8, "dte_max": 10, "exit_dte": 7}}
    book = backtest_fleet.backtest_option(spec, flat, PCFG, flat.index[24],
                                          iv_premium=1.15, opt_slip=0.015,
                                          r=0.04)
    assert book["trades"] == [] and book["position"] is None
