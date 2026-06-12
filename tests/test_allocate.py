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


def test_decayed_sharpe_float_noise_std_scores_zero():
    # returns recovered from compounding 1.01**i are constant up to ~1e-18
    # float jitter; the std floor must treat that as a flat book, not blow
    # up to a ~1e15 Sharpe
    values = [100 * 1.01 ** i for i in range(31)]
    rets = allocate.daily_returns([{"value": v} for v in values])
    assert allocate.decayed_sharpe(rets) == 0.0


def test_decay_weights_rejects_nonpositive_half_life():
    with pytest.raises(ValueError):
        allocate.decay_weights(5, half_life=0)
    with pytest.raises(ValueError):
        allocate.decay_weights(5, half_life=-10)


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
    # alternating returns give each book meaningfully nonzero std by
    # construction — the ordering must not hinge on cent-rounding noise
    books = {"loser": make_book([-0.012, -0.008] * 15),
             "winner": make_book([0.012, 0.008] * 15),
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


def test_nan_book_value_is_insufficient_and_never_ranks_first():
    # json round-trips NaN, so a corrupted mark can land in paper.json;
    # the NaN score must not float to rank 1 via arbitrary sort ordering
    corrupt = make_book([0.001 * (1 + i % 3) for i in range(30)])
    corrupt["history"][15]["value"] = float("nan")
    row = allocate.score_book(corrupt)
    assert row["insufficient"] is True
    assert row["score"] is None
    rows = allocate.rank_books(
        {"corrupt": corrupt, "winner": make_book([0.012, 0.008] * 15)})
    assert [r["strategy"] for r in rows] == ["winner", "corrupt"]


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


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_main_nonpositive_half_life_is_usage_error(tmp_path, monkeypatch,
                                                   capsys, bad):
    p = tmp_path / "paper.json"
    write_state(p, {"s": make_book([0.01] * 30)})
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p, "--half-life", bad)
    assert exc.value.code == 2  # argparse usage error, not a traceback
    assert "--half-life must be > 0" in capsys.readouterr().err


def test_main_table_marks_insufficient(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, {"newbie": make_book([0.01] * 3)})
    run_main(monkeypatch, p)
    assert "insufficient data" in capsys.readouterr().out


# --- Thompson sampling (slice 2) ------------------------------------------------

DOMINANT = [0.006, 0.002] * 30   # mean 0.004/day, 60 days of evidence
MEDIOCRE = [0.002, -0.002] * 30  # mean 0/day, same length
SPARSE = [0.05] * 5              # huge returns but insufficient data


def fleet_books():
    return {"dominant": make_book(DOMINANT),
            "mediocre": make_book(MEDIOCRE),
            "sparse": make_book(SPARSE)}


def test_posterior_params_scored_book_shrinks_with_n_eff():
    scored = allocate.score_book(make_book(DOMINANT))
    mean, std = allocate.posterior_params(scored)
    assert mean == pytest.approx(scored["mean"])
    assert std == pytest.approx(scored["std"] / math.sqrt(scored["n_eff"]))
    assert std < scored["std"]  # posterior on the mean is tighter than raw std


def test_posterior_params_insufficient_gets_diffuse_prior():
    scored = allocate.score_book(make_book(SPARSE))
    mean, std = allocate.posterior_params(scored)
    assert (mean, std) == (0.0, allocate.PRIOR_STD)


def test_pick_seed_derives_from_date_plus_offset():
    assert allocate.pick_seed("2026-06-12") == 20260612
    assert allocate.pick_seed("2026-06-12", seed=7) == 20260619


def test_thompson_pick_is_deterministic_for_same_date_and_books():
    a = allocate.thompson_pick(fleet_books(), "2026-06-12")
    b = allocate.thompson_pick(fleet_books(), "2026-06-12")
    assert a == b


def test_thompson_pick_independent_of_dict_insertion_order():
    books = fleet_books()
    reversed_books = dict(reversed(list(books.items())))
    assert (allocate.thompson_pick(books, "2026-06-12")
            == allocate.thompson_pick(reversed_books, "2026-06-12"))


def test_thompson_pick_changes_with_date_and_seed():
    base = allocate.thompson_pick(fleet_books(), "2026-06-12")
    other_date = allocate.thompson_pick(fleet_books(), "2026-06-13")
    other_seed = allocate.thompson_pick(fleet_books(), "2026-06-12", seed=1)
    draws = lambda r: [row["draw"] for row in r["rows"]]  # noqa: E731
    assert draws(base) != draws(other_date)
    assert draws(base) != draws(other_seed)


def test_thompson_pick_champion_is_highest_draw_and_weights_normalize():
    result = allocate.thompson_pick(fleet_books(), "2026-06-12")
    rows = result["rows"]
    assert result["champion"] == rows[0]["strategy"]
    assert [r["draw"] for r in rows] == sorted(
        (r["draw"] for r in rows), reverse=True)
    assert sum(r["weight"] for r in rows) == pytest.approx(1.0)
    assert [r["weight"] for r in rows] == sorted(
        (r["weight"] for r in rows), reverse=True)


def test_thompson_pick_marks_insufficient_but_keeps_it_eligible():
    result = allocate.thompson_pick(fleet_books(), "2026-06-12")
    by_name = {r["strategy"]: r for r in result["rows"]}
    assert by_name["sparse"]["insufficient"] is True
    assert by_name["sparse"]["post_std"] == allocate.PRIOR_STD
    assert by_name["dominant"]["insufficient"] is False


def test_thompson_pick_empty_books():
    result = allocate.thompson_pick({}, "2026-06-12")
    assert result["champion"] is None
    assert result["rows"] == []


def pick_dates(n=300, start="2025-01-01"):
    import datetime as dt
    d0 = dt.date.fromisoformat(start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def win_counts(books, dates):
    counts = {name: 0 for name in books}
    for d in dates:
        counts[allocate.thompson_pick(books, d)["champion"]] += 1
    return counts


def test_dominant_strategy_wins_majority_sparse_still_explores():
    dates = pick_dates()
    counts = win_counts(fleet_books(), dates)
    assert counts["dominant"] > len(dates) / 2   # exploitation
    assert counts["sparse"] > 0                  # exploration never dies
    # mediocre's posterior sits ~10 sigma below dominant's — it should
    # essentially never out-draw it
    assert counts["mediocre"] < counts["dominant"]


def test_dominant_win_share_grows_with_more_and_stronger_evidence():
    dates = pick_dates()
    before = win_counts(fleet_books(), dates)
    stronger = fleet_books()
    stronger["dominant"] = make_book([0.008, 0.004] * 75)  # longer + stronger
    after = win_counts(stronger, dates)
    assert after["dominant"] > before["dominant"]


# --- pick CLI -------------------------------------------------------------------

def test_main_pick_is_byte_identical_across_reruns(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12")
    first = capsys.readouterr().out
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12")
    assert capsys.readouterr().out == first
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json")
    first_json = capsys.readouterr().out
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json")
    assert capsys.readouterr().out == first_json


def test_main_pick_table_shows_champion_and_marks_insufficient(
        tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12")
    out = capsys.readouterr().out
    assert "champion:" in out
    assert "insufficient data (diffuse prior)" in out


def test_main_pick_json_shape(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--seed", "3",
             "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["date"] == "2026-06-12"
    assert out["seed"] == 3
    assert out["prior_std"] == allocate.PRIOR_STD
    assert out["champion"] in {"dominant", "mediocre", "sparse"}
    assert len(out["rows"]) == 3
    assert out["champion"] == out["rows"][0]["strategy"]
    for r in out["rows"]:
        for key in ("draw", "weight", "post_mean", "post_std", "days",
                    "insufficient"):
            assert key in r
    by_name = {r["strategy"]: r for r in out["rows"]}
    assert by_name["sparse"]["insufficient"] is True


def test_main_pick_seed_offset_changes_draws(tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json")
    a = json.loads(capsys.readouterr().out)
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--seed", "99",
             "--json")
    b = json.loads(capsys.readouterr().out)
    assert [r["draw"] for r in a["rows"]] != [r["draw"] for r in b["rows"]]


def test_main_pick_empty_books_says_nothing_to_pick(tmp_path, monkeypatch,
                                                    capsys):
    p = tmp_path / "paper.json"
    write_state(p, {})
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12")
    assert "nothing to pick" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["2026-13-01", "yesterday", "20260612"])
def test_main_pick_bad_date_is_usage_error(tmp_path, monkeypatch, capsys, bad):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p, "--pick", "--date", bad)
    assert exc.value.code == 2
    assert "--date must be YYYY-MM-DD" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [("--date", "2026-06-12"), ("--seed", "5")])
def test_main_date_or_seed_without_pick_is_usage_error(tmp_path, monkeypatch,
                                                       capsys, argv):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p, *argv)
    assert exc.value.code == 2
    assert "--date/--seed only apply with --pick" in capsys.readouterr().err
