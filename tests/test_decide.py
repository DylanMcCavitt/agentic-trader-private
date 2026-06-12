"""Tests for scripts/decide.py: rsi() math and the decision branches.

Hermetic: yfinance is never called. Price history is injected by replacing
decide.yf with a stub whose download() returns a fixture DataFrame, and
strategy params are controlled via decide.CONFIG. Production code is unchanged.
"""
import importlib.util
import json
import math
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("decide", ROOT / "scripts" / "decide.py")
decide = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decide)


# ---------------------------------------------------------------------------
# rsi() unit tests (period=2, matching the strategy)
# ---------------------------------------------------------------------------

def test_rsi_all_up_is_100():
    out = decide.rsi(pd.Series([100.0, 101.0, 102.0, 103.0, 104.0]), 2)
    valid = out.dropna()
    assert not valid.empty
    assert valid.tolist() == pytest.approx([100.0] * len(valid))


def test_rsi_all_down_is_0():
    out = decide.rsi(pd.Series([104.0, 103.0, 102.0, 101.0, 100.0]), 2)
    valid = out.dropna()
    assert not valid.empty
    assert valid.tolist() == pytest.approx([0.0] * len(valid))


def test_rsi_flat_is_nan():
    # No gains and no losses -> 0/0 -> NaN, by construction of the formula.
    out = decide.rsi(pd.Series([100.0, 100.0, 100.0, 100.0]), 2)
    assert out.dropna().empty


def test_rsi_respects_min_periods():
    out = decide.rsi(pd.Series([100.0, 101.0, 100.5, 101.5, 100.0]), 2)
    assert math.isnan(out.iloc[0])
    assert math.isnan(out.iloc[1])
    assert not math.isnan(out.iloc[2])


def test_rsi_hand_computed_mixed_series():
    # deltas: [nan, +1, -0.5, +1, -1.5]; ewm(alpha=1/2, adjust=True) weights
    # halve per step back, NaN-leading position included in the decay.
    #   t=2: avg_gain = (0.5*1)/(1.5) = 1/3, avg_loss = (1*0.5)/1.5 = 1/3
    #        -> RS = 1 -> RSI = 50
    #   t=3: avg_gain = (.25*1 + 1*1)/1.75 = 5/7, avg_loss = (.5*.5)/1.75 = 1/7
    #        -> RS = 5 -> RSI = 100 - 100/6 = 83.333...
    #   t=4: avg_gain = (.125*1 + .5*1)/1.875 = 1/3
    #        avg_loss = (.25*.5 + 1*1.5)/1.875 = 13/15
    #        -> RS = 5/13 -> RSI = 100 - 100*13/18 = 27.777...
    out = decide.rsi(pd.Series([100.0, 101.0, 100.5, 101.5, 100.0]), 2)
    assert out.iloc[2] == pytest.approx(50.0)
    assert out.iloc[3] == pytest.approx(100 - 100 / 6)
    assert out.iloc[4] == pytest.approx(100 - 100 * 13 / 18)


# ---------------------------------------------------------------------------
# Decision-logic tests (injected history, zero network)
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {"date", "symbol", "price", "rsi2", "sma_trend", "sma_exit",
                 "holding", "decision", "reason"}

# Small, hand-checkable windows for fixtures (production values stay in config.json).
TEST_CONFIG = {"symbol": "TEST", "sma_trend": 5, "sma_exit": 3, "entry_rsi": 60.0}


def make_history(closes, multiindex=False):
    """Build a yf.download-shaped daily OHLCV frame ending before today."""
    idx = pd.bdate_range(end=pd.Timestamp(date.today()) - pd.Timedelta(days=1),
                         periods=len(closes))
    df = pd.DataFrame({"Open": closes, "High": closes, "Low": closes,
                       "Close": closes, "Volume": [0] * len(closes)}, index=idx)
    if multiindex:
        df.columns = pd.MultiIndex.from_product([df.columns, ["TEST"]])
    return df


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Hard guard: any unpatched fetch attempt fails the test."""
    def _forbidden(*args, **kwargs):
        raise AssertionError("network fetch attempted during tests")
    monkeypatch.setattr(decide, "yf", SimpleNamespace(download=_forbidden))


@pytest.fixture
def run_decide(monkeypatch, capsys):
    def _run(history_df, price, holding, config=TEST_CONFIG):
        monkeypatch.setattr(
            decide, "yf", SimpleNamespace(download=lambda *a, **k: history_df))
        for key, val in config.items():
            monkeypatch.setitem(decide.CONFIG, key, val)
        monkeypatch.setattr(
            sys, "argv", ["decide.py", "--price", str(price), "--holding", holding])
        decide.main()
        return json.loads(capsys.readouterr().out)
    return _run


def test_buy_when_rsi_below_entry_and_above_trend(run_decide):
    # Uptrend with a two-day pullback that stays above the trend SMA.
    # px = [100 x7, 200, 300, 270, 240]:
    #   SMA5 = (100+200+300+270+240)/5 = 222 < close 240 (above trend)
    #   RSI2 = 100 - 100/(1 + (37.5/45)) = 500/11 = 45.45 < entry_rsi 60
    out = run_decide(make_history([100.0] * 7 + [200.0, 300.0, 270.0]),
                     price=240.0, holding="false")
    assert out["decision"] == "BUY"
    assert out["rsi2"] == pytest.approx(45.45)
    assert out["sma_trend"] == pytest.approx(222.0)
    assert out["price"] == pytest.approx(240.0)
    assert out["holding"] is False
    assert set(out) == EXPECTED_KEYS


def test_no_entry_below_trend_even_with_low_rsi(run_decide):
    # Straight decline: RSI2 = 0 (< entry) but close is far below the trend SMA.
    out = run_decide(make_history([300.0] * 7 + [200.0, 150.0, 120.0]),
                     price=110.0, holding="false")
    assert out["decision"] == "NONE"
    assert out["rsi2"] < decide.CONFIG["entry_rsi"]
    assert out["price"] < out["sma_trend"]


def test_no_entry_above_trend_when_rsi_too_high(run_decide):
    # Steady ascent: above trend but RSI2 = 100 (not oversold).
    out = run_decide(make_history([100.0 + i for i in range(10)]),
                     price=110.0, holding="false")
    assert out["decision"] == "NONE"
    assert out["price"] > out["sma_trend"]
    assert out["rsi2"] > decide.CONFIG["entry_rsi"]


def test_sell_when_holding_and_close_above_exit_sma(run_decide):
    # Ascending close 110 > SMA3 (108+109+110)/3 = 109 -> SELL.
    out = run_decide(make_history([100.0 + i for i in range(10)]),
                     price=110.0, holding="true")
    assert out["decision"] == "SELL"
    assert out["sma_exit"] == pytest.approx(109.0)
    assert out["holding"] is True


def test_hold_when_holding_and_close_at_or_below_exit_sma(run_decide):
    # Descending close 110 < SMA3 (112+111+110)/3 = 111 -> HOLD.
    out = run_decide(make_history([120.0 - i for i in range(10)]),
                     price=110.0, holding="true")
    assert out["decision"] == "HOLD"
    assert out["price"] < out["sma_exit"]
    assert out["holding"] is True


def test_holding_flag_selects_branch_on_identical_data(run_decide):
    # Same oversold-uptrend data: not holding -> BUY, holding -> exit logic.
    history = make_history([100.0] * 7 + [200.0, 300.0, 270.0])
    not_holding = run_decide(history, price=240.0, holding="false")
    holding = run_decide(history, price=240.0, holding="true")
    assert not_holding["decision"] == "BUY"
    # close 240 < SMA3 (300+270+240)/3 = 270 -> HOLD, never BUY while holding.
    assert holding["decision"] == "HOLD"


def test_multiindex_columns_are_flattened(run_decide):
    out = run_decide(make_history([100.0 + i for i in range(10)], multiindex=True),
                     price=110.0, holding="true")
    assert out["decision"] == "SELL"
    assert out["symbol"] == "TEST"


def test_insufficient_history_errors_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(
        decide, "yf",
        SimpleNamespace(download=lambda *a, **k: make_history([100.0] * 4)))
    for key, val in TEST_CONFIG.items():
        monkeypatch.setitem(decide.CONFIG, key, val)
    monkeypatch.setattr(sys, "argv", ["decide.py", "--price", "100", "--holding", "false"])
    with pytest.raises(SystemExit) as exc:
        decide.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "ERROR"
    assert "reason" in out


def test_invalid_holding_value_rejected(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["decide.py", "--price", "100", "--holding", "maybe"])
    with pytest.raises(SystemExit) as exc:
        decide.main()
    assert exc.value.code == 2
