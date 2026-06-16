"""Tests for scripts/decide_option.py: signal -> option action mapping.

Hermetic: the underlying signal is stubbed so the entry/exit -> OPEN/CLOSE/
HOLD/NONE mapping (and the exit_dte near-expiry override) is tested directly,
and yfinance is never called -- fetch_history is replaced with a fixture frame.
"""
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "decide_option", ROOT / "scripts" / "decide_option.py"
)
decide_option = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decide_option)

TODAY = date(2026, 6, 15)
SPEC = {"name": "opt_rsi2_call_aapl", "kind": "option", "symbol": "AAPL",
        "right": "call", "signal": "fake", "params": {"exit_dte": 7}}


def make_history():
    idx = pd.bdate_range(end=pd.Timestamp(TODAY) - pd.Timedelta(days=1), periods=4)
    closes = [100.0, 101.0, 102.0, 103.0]
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes,
         "Volume": [0] * 4}, index=idx,
    )


@pytest.fixture(autouse=True)
def stub_history(monkeypatch):
    monkeypatch.setattr(decide_option, "fetch_history", lambda symbol: make_history())


def set_signal(monkeypatch, *, entry=False, exit=False):
    monkeypatch.setattr(decide_option, "SIGNALS",
                        {"fake": lambda df, p: {"entry": entry, "exit": exit,
                                                "reason": "stub"}})


def test_flat_entry_opens(monkeypatch):
    set_signal(monkeypatch, entry=True)
    out = decide_option.compute_option_decision(SPEC, 207.5, False, TODAY)
    assert out["decision"] == "OPEN"
    assert out["spot"] == 207.5 and out["symbol"] == "AAPL" and out["right"] == "call"


def test_flat_no_entry_is_none(monkeypatch):
    set_signal(monkeypatch, entry=False)
    out = decide_option.compute_option_decision(SPEC, 207.5, False, TODAY)
    assert out["decision"] == "NONE"


def test_holding_exit_signal_closes(monkeypatch):
    set_signal(monkeypatch, exit=True)
    out = decide_option.compute_option_decision(SPEC, 207.5, True, TODAY,
                                                expiry="2026-08-01")
    assert out["decision"] == "CLOSE"
    assert "exit signal" in out["reason"]


def test_holding_no_exit_far_expiry_holds(monkeypatch):
    set_signal(monkeypatch, exit=False)
    out = decide_option.compute_option_decision(SPEC, 207.5, True, TODAY,
                                                expiry="2026-08-01")
    assert out["decision"] == "HOLD"


def test_holding_near_expiry_closes_even_without_exit(monkeypatch):
    set_signal(monkeypatch, exit=False)
    # 2026-06-18 is 3 DTE, within exit_dte=7
    out = decide_option.compute_option_decision(SPEC, 207.5, True, TODAY,
                                                expiry="2026-06-18")
    assert out["decision"] == "CLOSE"
    assert "DTE" in out["reason"]


def test_holding_no_expiry_falls_back_to_signal(monkeypatch):
    set_signal(monkeypatch, exit=False)
    out = decide_option.compute_option_decision(SPEC, 207.5, True, TODAY)
    assert out["decision"] == "HOLD"


def test_non_option_spec_raises(monkeypatch):
    set_signal(monkeypatch, entry=True)
    equity = {**SPEC, "kind": "equity"}
    with pytest.raises(ValueError):
        decide_option.compute_option_decision(equity, 207.5, False, TODAY)


def test_main_unknown_strategy_errors(monkeypatch, capsys):
    monkeypatch.setattr(decide_option, "load_config", lambda: {"strategies": {}})
    monkeypatch.setattr(sys, "argv",
                        ["decide_option.py", "--strategy", "nope",
                         "--holding", "false", "--price", "100"])
    with pytest.raises(SystemExit) as exc:
        decide_option.main()
    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "ERROR"


def test_main_open_path_emits_decision(monkeypatch, capsys):
    set_signal(monkeypatch, entry=True)
    monkeypatch.setattr(decide_option, "load_config",
                        lambda: {"strategies": {"opt_rsi2_call_aapl": SPEC}})
    monkeypatch.setattr(sys, "argv",
                        ["decide_option.py", "--strategy", "opt_rsi2_call_aapl",
                         "--holding", "false", "--price", "207.5",
                         "--quote-ts", "2026-06-15T15:45:00-04:00",
                         "--date", "2026-06-15"])
    decide_option.main()
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "OPEN"
    assert out["quote_ts"] == "2026-06-15T15:45:00-04:00"
