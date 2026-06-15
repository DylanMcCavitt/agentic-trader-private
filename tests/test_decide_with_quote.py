"""Tests for scripts/decide_with_quote.py quote persistence wrapper."""
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
    "decide_with_quote", ROOT / "scripts" / "decide_with_quote.py"
)
decide_with_quote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(decide_with_quote)
decide = decide_with_quote.decide

TEST_CONFIG = {"symbol": "TEST", "sma_trend": 5, "sma_exit": 3, "entry_rsi": 60.0}


def make_history(closes):
    idx = pd.bdate_range(end=pd.Timestamp(date.today()) - pd.Timedelta(days=1), periods=len(closes))
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [0] * len(closes)},
        index=idx,
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def _forbidden(*args, **kwargs):
        raise AssertionError("network fetch attempted during tests")
    monkeypatch.setattr(decide, "yf", SimpleNamespace(download=_forbidden))


def quote_record():
    return {"symbol": "TEST", "price": 240.0, "ts": "2026-06-10T10:25:00-04:00"}


def test_wrapper_persists_quote_used_for_decision(tmp_path, monkeypatch, capsys):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.json").write_text(json.dumps({"halt": False}))
    monkeypatch.setattr(decide_with_quote, "ROOT", tmp_path)
    monkeypatch.setattr(
        decide,
        "yf",
        SimpleNamespace(download=lambda *a, **k: make_history([100.0] * 7 + [200.0, 300.0, 270.0])),
    )
    for key, val in TEST_CONFIG.items():
        monkeypatch.setitem(decide.CONFIG, key, val)

    quote_json = json.dumps({
        "results": [{
            "symbol": "TEST",
            "last_trade_price": "240.00",
            "last_trade_at": "2026-06-10T10:25:00-04:00",
        }]
    })
    monkeypatch.setattr(
        sys,
        "argv",
        ["decide_with_quote.py", "--quote-json", quote_json, "--holding", "false"],
    )

    decide_with_quote.main()

    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "BUY"
    assert out["price"] == pytest.approx(240.0)
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    assert state["last_quote"] == quote_record()


def test_persist_last_quote_requires_existing_state_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        decide_with_quote.persist_last_quote(tmp_path, quote_record())


def test_persist_last_quote_rejects_non_object_state(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.json").write_text("[]")
    with pytest.raises(ValueError):
        decide_with_quote.persist_last_quote(tmp_path, quote_record())


def test_decision_failure_does_not_refresh_last_quote(tmp_path, monkeypatch, capsys):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "state.json").write_text(json.dumps({"halt": False}))
    monkeypatch.setattr(decide_with_quote, "ROOT", tmp_path)
    monkeypatch.setattr(decide, "compute_decision", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    for key, val in TEST_CONFIG.items():
        monkeypatch.setitem(decide.CONFIG, key, val)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "decide_with_quote.py",
            "--price",
            "240",
            "--quote-ts",
            "2026-06-10T10:25:00-04:00",
            "--holding",
            "false",
        ],
    )

    with pytest.raises(SystemExit) as exc:
        decide_with_quote.main()

    assert exc.value.code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "ERROR"
    state = json.loads((tmp_path / "state" / "state.json").read_text())
    assert "last_quote" not in state
