"""Tests for scripts/allocate.py — decay math, ranking, insufficient data,
and the missing/empty paper.json paths. Hermetic: no network, tmp_path only."""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import allocate  # noqa: E402


def make_book(returns, start=10000.0, first_day=1):
    """Synthetic paper book whose history compounds the given daily returns."""
    history = [{"date": f"d{first_day}", "value": round(start, 2)}]
    v = start
    for i, r in enumerate(returns, first_day + 1):
        v *= 1 + r
        history.append({"date": f"d{i}", "value": round(v, 2)})
    return {"cash": v, "starting_cash": start, "started": "d1",
            "position": None, "trades": [], "history": history}


def write_state(path, books, last_run="2026-06-12"):
    path.write_text(json.dumps({"last_run_date": last_run, "books": books}))


# --- decay math ---------------------------------------------------------------

def test_decay_weights_newest_is_one_and_halves_at_half_life():
    w = allocate.decay_weights(11, half_life=10)
    assert w[-1] == pytest.approx(1.0)
    assert w[0] == pytest.approx(0.5)  # 10 days older -> half weight
    assert w == sorted(w)  # oldest weighs least


def test_daily_returns_from_history():
    book = make_book([0.01, -0.02])
    rets = allocate.daily_returns(book["history"])
    assert rets == pytest.approx([0.01, -0.02], abs=1e-5)


def test_decayed_stats_match_unweighted_for_huge_half_life():
    rets = [0.01, -0.005, 0.02, 0.0]
    s = allocate.decayed_stats(rets, half_life=1e9)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    assert s["mean"] == pytest.approx(mean)
    assert s["var"] == pytest.approx(var)
    assert s["n_eff"] == pytest.approx(len(rets))


def test_decayed_mean_favors_recent_returns():
    recent_good = allocate.decayed_stats([0.0] * 50 + [0.01] * 10, half_life=5)
    old_good = allocate.decayed_stats([0.01] * 10 + [0.0] * 50, half_life=5)
    assert recent_good["mean"] > old_good["mean"]


def test_n_eff_shrinks_with_decay():
    full = allocate.decayed_stats([0.01, 0.02] * 30, half_life=1e9)["n_eff"]
    decayed = allocate.decayed_stats([0.01, 0.02] * 30, half_life=5)["n_eff"]
    assert decayed < full
    assert decayed > 1.0


def test_decayed_sharpe_annualizes():
    rets = [0.01, -0.01] * 15
    s = allocate.decayed_stats(rets, half_life=1e9)
    expected = s["mean"] / s["std"] * math.sqrt(252)
    assert allocate.decayed_sharpe(rets, half_life=1e9) == pytest.approx(expected)


def test_decayed_sharpe_zero_std_scores_zero():
    assert allocate.decayed_sharpe([0.0] * 30) == 0.0


def test_losing_streak_erodes_score_within_days():
    base = [0.005] * 60
    before = allocate.decayed_sharpe(base, half_life=10)
    after = allocate.decayed_sharpe(base + [-0.02] * 5, half_life=10)
    assert after < before


# --- ranking ------------------------------------------------------------------

def test_recent_performance_outranks_equal_but_stale_performance():
    good, flat = [0.01] * 10, [0.0] * 30
    books = {"stale": make_book(good + flat),   # same returns, good ones old
             "fresh": make_book(flat + good)}   # good ones recent
    rows = allocate.rank_books(books, half_life=10)
    assert [r["strategy"] for r in rows] == ["fresh", "stale"]
    assert rows[0]["score"] > rows[1]["score"]


def test_rank_orders_by_score_desc_and_insufficient_last():
    books = {"loser": make_book([-0.01] * 30),
             "winner": make_book([0.01] * 30),
             "newbie": make_book([0.05] * 5)}  # huge returns but too few days
    rows = allocate.rank_books(books)
    assert [r["strategy"] for r in rows] == ["winner", "loser", "newbie"]
    assert rows[-1]["insufficient"] is True
    assert rows[-1]["score"] is None


def test_insufficient_book_is_flagged_not_scored():
    row = allocate.score_book(make_book([0.01] * (allocate.MIN_RETURNS - 1)))
    assert row["insufficient"] is True
    assert row["score"] is None and row["mean"] is None and row["std"] is None
    assert row["days"] == allocate.MIN_RETURNS - 1


def test_book_at_minimum_window_is_scored():
    row = allocate.score_book(make_book([0.01] * allocate.MIN_RETURNS))
    assert row["insufficient"] is False
    assert row["score"] is not None


def test_empty_history_book_is_insufficient():
    book = {"history": [], "trades": []}
    row = allocate.score_book(book)
    assert row["insufficient"] is True
    assert row["days"] == 0


# --- CLI ----------------------------------------------------------------------

def run_main(monkeypatch, paper_path, *argv):
    monkeypatch.setattr(allocate, "PAPER_PATH", paper_path)
    monkeypatch.setattr(sys, "argv", ["allocate.py", *argv])
    allocate.main()


def test_main_missing_paper_json_exits_nonzero(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, tmp_path / "paper.json")
    assert exc.value.code == 1
    assert "no paper state" in capsys.readouterr().err


def test_main_corrupt_paper_json_exits_nonzero(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    p.write_text("")  # empty file -> not valid JSON
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p)
    assert exc.value.code == 1
    assert "unreadable" in capsys.readouterr().err


def test_main_empty_books_prints_header_only(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, {})
    run_main(monkeypatch, p)
    out = capsys.readouterr().out
    assert "allocator ranking" in out


def test_main_json_output_shape(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, {"winner": make_book([0.01] * 30),
                    "newbie": make_book([0.02] * 3)})
    run_main(monkeypatch, p, "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == "2026-06-12"
    assert out["half_life_days"] == allocate.HALF_LIFE_DAYS
    assert [r["strategy"] for r in out["rows"]] == ["winner", "newbie"]
    winner = out["rows"][0]
    assert winner["insufficient"] is False
    for key in ("score", "mean", "std", "n_eff", "days"):
        assert winner[key] is not None
    newbie = out["rows"][1]
    assert newbie["insufficient"] is True
    assert newbie["score"] is None
    assert newbie["days"] == 3


def test_main_half_life_flag_changes_scores(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, {"s": make_book([0.0] * 30 + [0.01] * 30)})
    run_main(monkeypatch, p, "--json", "--half-life", "5")
    short = json.loads(capsys.readouterr().out)["rows"][0]["score"]
    run_main(monkeypatch, p, "--json", "--half-life", "500")
    long = json.loads(capsys.readouterr().out)["rows"][0]["score"]
    assert short > long  # short half-life concentrates on the recent good run


def test_main_table_marks_insufficient(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, {"newbie": make_book([0.01] * 3)})
    run_main(monkeypatch, p)
    assert "insufficient data" in capsys.readouterr().out
