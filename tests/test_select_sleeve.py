"""Tests for scripts/select_sleeve.py: option-sleeve strategy selection.

Hermetic: no paper.json or config files are read; cfg and books are passed
straight to select(). The contract is "always return a tradeable strategy,
never block on paper history" -- the cold-start default path is the important
one because fresh books never clear min_score_days.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "select_sleeve", ROOT / "scripts" / "select_sleeve.py"
)
select_sleeve = importlib.util.module_from_spec(spec)
spec.loader.exec_module(select_sleeve)


def cfg_with(candidates, default, *, enabled=True, min_score_days=3, extra=None):
    strategies = {}
    for name in candidates:
        strategies[name] = {"enabled": True, "kind": "option",
                            "symbol": name.split("_")[-1].upper(), "right": "call",
                            "signal": "rsi2_long", "params": {}}
    if extra:
        strategies.update(extra)
    return {"option_sleeve": {"enabled": enabled, "default": default,
                              "candidates": candidates,
                              "min_score_days": min_score_days},
            "strategies": strategies}


def book(values):
    return {"history": [{"value": v} for v in values], "position": None}


def test_cold_start_uses_configured_default():
    cfg = cfg_with(["opt_x", "opt_y"], "opt_y")
    out = select_sleeve.select(cfg, {}, min_score_days=3)
    assert out["strategy"] == "opt_y"
    assert out["basis"] == "default"
    assert out["symbol"] == "Y"


def test_default_fallback_when_default_not_a_candidate():
    cfg = cfg_with(["opt_a", "opt_b"], "not_a_candidate")
    out = select_sleeve.select(cfg, {}, min_score_days=3)
    assert out["strategy"] == "opt_a"  # first candidate
    assert out["basis"] == "default_fallback"


def test_best_score_when_one_candidate_is_scored():
    cfg = cfg_with(["opt_a", "opt_b"], "opt_a")
    books = {"opt_b": book([100, 101, 102, 103, 104, 105])}  # >= 3 returns
    out = select_sleeve.select(cfg, books, min_score_days=3)
    assert out["strategy"] == "opt_b"
    assert out["basis"] == "best_score"
    assert out["score"] is not None and out["days"] == 5


def test_higher_sharpe_wins_between_two_scored():
    cfg = cfg_with(["opt_smooth", "opt_choppy"], "opt_smooth")
    books = {
        "opt_smooth": book([100, 101, 102, 103, 104, 105]),   # low variance, high Sharpe
        "opt_choppy": book([100, 103, 99, 104, 98, 105]),     # whipsaw, low Sharpe
    }
    out = select_sleeve.select(cfg, books, min_score_days=3)
    assert out["strategy"] == "opt_smooth"
    assert out["basis"] == "best_score"


def test_tie_breaks_alphabetically():
    cfg = cfg_with(["opt_b", "opt_a"], "opt_b")
    same = [100, 101, 102, 103, 104, 105]
    out = select_sleeve.select(cfg, {"opt_a": book(same), "opt_b": book(same)},
                               min_score_days=3)
    assert out["strategy"] == "opt_a"


def test_no_candidates_returns_none():
    cfg = {"option_sleeve": {"enabled": True, "default": "x", "candidates": []},
           "strategies": {}}
    out = select_sleeve.select(cfg, {}, min_score_days=3)
    assert out["strategy"] is None
    assert out["basis"] == "none"


def test_disabled_or_wrong_kind_candidates_are_ignored():
    cfg = cfg_with(["opt_a"], "opt_a", extra={
        "opt_disabled": {"enabled": False, "kind": "option", "symbol": "D",
                         "right": "call", "signal": "rsi2_long", "params": {}},
        "opt_equity": {"enabled": True, "kind": "equity", "symbol": "E",
                       "signal": "rsi2_long", "params": {}},
    })
    cfg["option_sleeve"]["candidates"] = ["opt_disabled", "opt_equity", "opt_a"]
    cfg["option_sleeve"]["default"] = "opt_a"
    assert [n for n, _ in select_sleeve.candidate_specs(cfg)] == ["opt_a"]


def test_load_books_missing_file_is_empty(tmp_path):
    assert select_sleeve.load_books(tmp_path / "nope.json") == {}


def test_load_books_corrupt_file_is_empty(tmp_path):
    p = tmp_path / "paper.json"
    p.write_text("{not json")
    assert select_sleeve.load_books(p) == {}


def test_main_disabled_sleeve_prints_null(monkeypatch, capsys):
    monkeypatch.setattr(select_sleeve, "load_config",
                        lambda: cfg_with(["opt_a"], "opt_a", enabled=False))
    monkeypatch.setattr(sys, "argv", ["select_sleeve.py"])
    select_sleeve.main()
    out = json.loads(capsys.readouterr().out)
    assert out["strategy"] is None and out["basis"] == "disabled"


def test_main_enabled_prints_default(monkeypatch, capsys):
    monkeypatch.setattr(select_sleeve, "load_config",
                        lambda: cfg_with(["opt_a", "opt_b"], "opt_b"))
    monkeypatch.setattr(select_sleeve, "load_books", lambda: {})
    monkeypatch.setattr(sys, "argv", ["select_sleeve.py"])
    select_sleeve.main()
    out = json.loads(capsys.readouterr().out)
    assert out["strategy"] == "opt_b" and out["basis"] == "default"
