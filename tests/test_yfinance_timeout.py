"""yf.download calls pass an explicit, shared timeout."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backtest  # noqa: E402
import decide  # noqa: E402
from strategies import common  # noqa: E402
from yfinance_utils import (  # noqa: E402
    DEFAULT_YFINANCE_DOWNLOAD_TIMEOUT_SECONDS,
    YFINANCE_DOWNLOAD_TIMEOUT_ENV,
    yfinance_download_timeout,
)


def make_history(rows: int = 20) -> pd.DataFrame:
    idx = pd.bdate_range(end=pd.Timestamp(date.today()) - pd.Timedelta(days=1), periods=rows)
    closes = [100.0 + i for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": [0] * rows,
        },
        index=idx,
    )


def test_yfinance_download_timeout_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv(YFINANCE_DOWNLOAD_TIMEOUT_ENV, raising=False)

    assert yfinance_download_timeout() == DEFAULT_YFINANCE_DOWNLOAD_TIMEOUT_SECONDS


@pytest.mark.parametrize("raw", ["", "0", "-1", "nan", "NaN", "inf", "-inf", "abc"])
def test_yfinance_download_timeout_rejects_invalid_env(monkeypatch, raw: str):
    monkeypatch.setenv(YFINANCE_DOWNLOAD_TIMEOUT_ENV, raw)

    with pytest.raises(ValueError):
        yfinance_download_timeout()


def test_decide_download_uses_env_timeout(monkeypatch, capsys):
    calls = []

    def fake_download(*args, **kwargs):
        calls.append((args, kwargs))
        return make_history()

    monkeypatch.setenv("AGENTIC_TRADER_YFINANCE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setattr(decide, "yf", SimpleNamespace(download=fake_download))
    for key, val in {
        "symbol": "TEST",
        "sma_trend": 5,
        "sma_exit": 3,
        "entry_rsi": 60.0,
    }.items():
        monkeypatch.setitem(decide.CONFIG, key, val)
    monkeypatch.setattr(
        sys,
        "argv",
        ["decide.py", "--price", "125", "--holding", "false"],
    )

    decide.main()

    capsys.readouterr()
    assert calls[0][1]["timeout"] == 2.5


def test_backtest_fetch_download_uses_env_timeout(monkeypatch):
    calls = []

    def fake_download(*args, **kwargs):
        calls.append((args, kwargs))
        return make_history()

    monkeypatch.setenv("AGENTIC_TRADER_YFINANCE_TIMEOUT_SECONDS", "3")
    monkeypatch.setattr(backtest, "yf", SimpleNamespace(download=fake_download))

    df = backtest.fetch("SPY")

    assert not df.empty
    assert calls[0][1]["timeout"] == 3.0


def test_strategy_common_fetch_history_download_uses_env_timeout(monkeypatch):
    calls = []

    def fake_download(*args, **kwargs):
        calls.append((args, kwargs))
        return make_history()

    monkeypatch.setenv("AGENTIC_TRADER_YFINANCE_TIMEOUT_SECONDS", "4")
    monkeypatch.setattr(common, "yf", SimpleNamespace(download=fake_download))

    df = common.fetch_history("QQQ", period="1y")

    assert not df.empty
    assert calls[0][1]["timeout"] == 4.0
