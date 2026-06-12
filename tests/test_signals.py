"""Tests for scripts/strategies/signals.py — every signal's entry/exit logic.

Hermetic: frames are built by hand, no network. Fixtures use small,
hand-checkable indicator windows (production values stay in config.json).
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from strategies import SIGNALS, signals  # noqa: E402


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


def test_registry_covers_all_signal_functions():
    assert set(SIGNALS) == {"rsi2_long", "ibs_long", "bollinger_long",
                            "donchian_long", "rsi2_short", "donchian_short"}


# --- rsi2_long (same fixture family as tests/test_decide.py) ---------------

RSI2_P = {"sma_trend": 5, "sma_exit": 3, "entry_rsi": 60.0}


def test_rsi2_long_entry_on_pullback_above_trend():
    # px = [100 x7, 200, 300, 270, 240]: SMA5 222 < 240, RSI2 45.45 < 60.
    out = signals.rsi2_long(make_df([100] * 7 + [200, 300, 270, 240]), RSI2_P)
    assert out["entry"] is True
    assert out["exit"] is False  # close 240 < SMA3 270
    assert out["metrics"]["rsi2"] == pytest.approx(45.45, abs=0.01)
    assert out["metrics"]["sma_trend"] == pytest.approx(222.0)


def test_rsi2_long_no_entry_below_trend():
    out = signals.rsi2_long(make_df([300] * 7 + [200, 150, 120, 110]), RSI2_P)
    assert out["entry"] is False
    assert out["metrics"]["rsi2"] < RSI2_P["entry_rsi"]  # oversold but in decline


def test_rsi2_long_exit_above_exit_sma():
    out = signals.rsi2_long(make_df([100 + i for i in range(11)]), RSI2_P)
    assert out["exit"] is True  # close 110 > SMA3 109
    assert out["entry"] is False  # RSI2 = 100


# --- rsi2_short -------------------------------------------------------------

RSI2_SHORT_P = {"sma_trend": 15, "entry_rsi": 90.0, "exit_rsi": 30.0}


def test_rsi2_short_entry_on_rally_below_trend():
    # Old 400 plateau keeps SMA15 above the close; two tiny up days max out RSI2.
    out = signals.rsi2_short(make_df([400] * 3 + [390] * 10 + [391, 392]),
                             RSI2_SHORT_P)
    assert out["metrics"]["rsi2"] > 90
    assert out["metrics"]["close"] < out["metrics"]["sma_trend"]
    assert out["entry"] is True
    assert out["exit"] is False


def test_rsi2_short_exit_when_oversold():
    out = signals.rsi2_short(make_df([400 - 5 * i for i in range(16)]),
                             RSI2_SHORT_P)
    assert out["metrics"]["rsi2"] == pytest.approx(0.0)
    assert out["exit"] is True


# --- ibs_long ---------------------------------------------------------------

IBS_P = {"sma_trend": 5, "sma_exit": 3, "entry_ibs": 0.2, "exit_ibs": 0.8}


def test_ibs_long_entry_near_low_of_range():
    # Today: H 110 / L 100 / C 101 -> IBS 0.1; close above short uptrend SMA.
    df = make_df([90] * 9 + [101], highs=[90] * 9 + [110], lows=[90] * 9 + [100])
    out = signals.ibs_long(df, IBS_P)
    assert out["metrics"]["ibs"] == pytest.approx(0.1)
    assert out["entry"] is True


def test_ibs_long_no_entry_near_high_of_range():
    df = make_df([90] * 9 + [109], highs=[90] * 9 + [110], lows=[90] * 9 + [100])
    out = signals.ibs_long(df, IBS_P)
    assert out["metrics"]["ibs"] == pytest.approx(0.9)
    assert out["entry"] is False
    assert out["exit"] is True  # IBS 0.9 > 0.8


def test_ibs_flat_bar_is_neutral():
    out = signals.ibs_long(make_df([100] * 10), IBS_P)
    assert out["metrics"]["ibs"] == pytest.approx(0.5)
    assert out["entry"] is False


# --- bollinger_long ---------------------------------------------------------

def test_bollinger_long_entry_below_band_in_uptrend():
    # Steady uptrend, then a sharp one-day dip below the 2-sigma band that
    # stays above the long SMA. Expectations computed with the same pandas
    # ops, explicitly, so the assertion is a wiring check.
    closes = [100 + 0.5 * i for i in range(60)] + [118.2]
    p = {"sma_trend": 50, "bb_len": 20, "bb_std": 2.0}
    px = pd.Series(closes, dtype=float)
    mid = px.rolling(20).mean().iloc[-1]
    lower = mid - 2.0 * px.rolling(20).std().iloc[-1]
    trend = px.rolling(50).mean().iloc[-1]
    out = signals.bollinger_long(make_df(closes), p)
    assert out["entry"] is bool(118.2 > trend and 118.2 < lower)
    assert out["entry"] is True  # the fixture is built to fire
    assert out["exit"] is False
    assert out["metrics"]["bb_lower"] == pytest.approx(round(lower, 2))


def test_bollinger_long_exit_at_mid_band():
    closes = [100] * 25  # flat: mid == close -> close >= mid fires
    out = signals.bollinger_long(make_df(closes),
                                 {"sma_trend": 10, "bb_len": 20, "bb_std": 2.0})
    assert out["exit"] is True
    assert out["entry"] is False


# --- donchian_long / donchian_short ----------------------------------------

def test_donchian_long_breakout_entry():
    # Prior 20d high is 110; today closes at 111.
    highs = [110] * 24 + [112]
    closes = [105] * 24 + [111]
    out = signals.donchian_long(make_df(closes, highs=highs, lows=closes),
                                {"entry_high": 20, "exit_low": 10})
    assert out["metrics"]["channel_high"] == pytest.approx(110.0)
    assert out["entry"] is True
    assert out["exit"] is False


def test_donchian_long_exit_on_breakdown():
    lows = [100] * 24 + [95]
    closes = [105] * 24 + [99]
    out = signals.donchian_long(make_df(closes, highs=closes, lows=lows),
                                {"entry_high": 20, "exit_low": 10})
    assert out["metrics"]["channel_low"] == pytest.approx(100.0)
    assert out["exit"] is True
    assert out["entry"] is False


def test_donchian_short_breakdown_needs_downtrend():
    p = {"entry_low": 20, "exit_high": 10, "sma_trend": 5}
    lows = [100] * 24 + [95]
    # Below the prior 20d low AND below SMA5 -> entry.
    closes_down = [105] * 24 + [99]
    out = signals.donchian_short(make_df(closes_down, highs=closes_down, lows=lows), p)
    assert out["entry"] is True
    # Same breakdown bar but the close sits above the trend SMA -> no entry.
    closes_up = [90] * 24 + [99]
    out2 = signals.donchian_short(make_df(closes_up, highs=closes_up, lows=lows), p)
    assert out2["entry"] is False


def test_donchian_short_exit_over_channel_high():
    p = {"entry_low": 20, "exit_high": 10, "sma_trend": 5}
    highs = [110] * 24 + [112]
    closes = [105] * 24 + [111]
    out = signals.donchian_short(make_df(closes, highs=highs, lows=closes), p)
    assert out["exit"] is True


# --- momentum_rotation ------------------------------------------------------

def test_rotation_picks_strongest_positive():
    dfs = {"SPY": make_df([100, 100, 100, 110]),   # +10% over lookback 3
           "QQQ": make_df([100, 100, 100, 120])}   # +20%
    out = signals.momentum_rotation(dfs, {"lookback": 3})
    assert out["target"] == "QQQ"


def test_rotation_goes_to_cash_when_leader_negative():
    dfs = {"SPY": make_df([100, 100, 100, 90]),
           "QQQ": make_df([100, 100, 100, 80])}
    out = signals.momentum_rotation(dfs, {"lookback": 3})
    assert out["target"] is None


def test_rotation_insufficient_history_is_cash():
    dfs = {"SPY": make_df([100, 101])}
    out = signals.momentum_rotation(dfs, {"lookback": 126})
    assert out["target"] is None
    assert "bars" in out["reason"]
