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
    # hermetic: --pick reads the hysteresis incumbent from ALLOC_PATH, so it
    # must never point at a developer's real state/allocator.json here
    monkeypatch.setattr(allocate, "ALLOC_PATH",
                        paper_path.parent / "allocator.json")
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


def test_posterior_params_float_noise_flat_book_gets_diffuse_prior():
    # constant compounding picks up ~1e-18 float jitter: std is below
    # STD_FLOOR, which decayed_sharpe already treats as "no evidence either
    # way" (scores 0.0). Without the same floor here the posterior would be
    # a delta function at the mean — winning every Thompson draw with zero
    # exploration while the ranking view ranks the book below real winners.
    flat = {"history": [{"date": f"d{i}", "value": 100 * 1.005 ** i}
                        for i in range(31)]}
    scored = allocate.score_book(flat)
    assert scored["insufficient"] is False
    assert scored["std"] <= allocate.STD_FLOOR
    assert (allocate.posterior_params(scored)
            == (0.0, allocate.PRIOR_STD))


def test_pick_seed_folds_offset_without_aliasing_adjacent_dates():
    assert allocate.pick_seed("2026-06-12") == "20260612:0"
    assert allocate.pick_seed("2026-06-12", seed=7) == "20260612:7"
    # a seeded re-roll must never reproduce another date's RNG stream
    assert allocate.pick_seed("2026-06-12", seed=1) != allocate.pick_seed(
        "2026-06-13", seed=0)


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
    # seed offsets must not alias adjacent dates: seed 1 today is an
    # independent draw, not tomorrow's seed-0 draws consumed early
    assert draws(other_date) != draws(other_seed)


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


def test_thompson_pick_all_insufficient_books_still_picks():
    # launch day: the whole fleet is on the diffuse prior — every row is
    # flagged insufficient, draws come from N(0, PRIOR_STD^2), and a
    # champion is still selected with normalized weights
    books = {"a": make_book(SPARSE), "b": make_book([0.01] * 4),
             "c": make_book([])}
    result = allocate.thompson_pick(books, "2026-06-12")
    assert result["champion"] in books
    assert all(r["insufficient"] for r in result["rows"])
    assert all(r["post_mean"] == 0.0 and r["post_std"] == allocate.PRIOR_STD
               for r in result["rows"])
    assert sum(r["weight"] for r in result["rows"]) == pytest.approx(1.0)


def test_thompson_pick_single_strategy_gets_full_weight():
    result = allocate.thompson_pick({"only": make_book(DOMINANT)},
                                    "2026-06-12")
    assert result["champion"] == "only"
    assert len(result["rows"]) == 1
    assert result["rows"][0]["weight"] == pytest.approx(1.0)


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


# --- switch hysteresis (issue #41) ------------------------------------------------

def test_incumbent_none_is_plain_highest_draw():
    result = allocate.thompson_pick(fleet_books(), "2026-06-12")
    assert result["incumbent"] is None
    assert result["hysteresis"] == allocate.HYSTERESIS
    assert result["champion"] == result["rows"][0]["strategy"]


def test_hysteresis_zero_with_incumbent_reproduces_plain_pick():
    # h=0 must reproduce the slice-2 highest-draw behavior exactly, with the
    # incumbent threaded day over day like the live CLI does
    books = fleet_books()
    incumbent = "mediocre"
    for d in pick_dates(60):
        plain = allocate.thompson_pick(books, d)
        gated = allocate.thompson_pick(books, d, incumbent=incumbent,
                                       hysteresis=0.0)
        assert gated["champion"] == plain["champion"]
        assert gated["rows"] == plain["rows"]  # draws/weights untouched
        incumbent = gated["champion"]


def test_incumbent_retained_when_challenger_within_margin():
    # with an effectively infinite margin, a scored incumbent survives any
    # out-draw; rows and weights still rank by raw draw
    books = fleet_books()
    for d in pick_dates(100):
        plain = allocate.thompson_pick(books, d)
        if plain["champion"] == "dominant":
            gated = allocate.thompson_pick(books, d, incumbent="mediocre",
                                           hysteresis=1e9)
            assert gated["champion"] == "mediocre"
            assert gated["rows"] == plain["rows"]
            assert gated["rows"][0]["strategy"] == "dominant"
            return
    pytest.fail("no date where dominant out-drew mediocre")


def test_displacement_threshold_is_h_incumbent_posterior_stds():
    # the margin is scale-aware: challenger wins exactly when its draw beats
    # the incumbent's by more than h * incumbent_posterior_std
    books = fleet_books()
    for d in pick_dates(100):
        plain = allocate.thompson_pick(books, d)
        top = plain["rows"][0]
        inc = next(r for r in plain["rows"] if r["strategy"] == "mediocre")
        if top["strategy"] != "mediocre" and top["draw"] > inc["draw"]:
            gap_h = (top["draw"] - inc["draw"]) / inc["post_std"]
            kept = allocate.thompson_pick(books, d, incumbent="mediocre",
                                          hysteresis=gap_h * 1.01)
            lost = allocate.thompson_pick(books, d, incumbent="mediocre",
                                          hysteresis=gap_h * 0.99)
            assert kept["champion"] == "mediocre"
            assert lost["champion"] == top["strategy"]
            return
    pytest.fail("no date where mediocre was out-drawn")


def test_insufficient_incumbent_is_displaceable_despite_huge_margin():
    # an insufficient-data incumbent gets no hysteresis protection — same
    # displaceability as before
    books = fleet_books()
    for d in pick_dates(100):
        plain = allocate.thompson_pick(books, d)
        if plain["champion"] != "sparse":
            gated = allocate.thompson_pick(books, d, incumbent="sparse",
                                           hysteresis=1e9)
            assert gated["champion"] == plain["champion"]
            return
    pytest.fail("sparse won every draw — fixture broken")


def test_flat_book_incumbent_is_displaceable_despite_huge_margin():
    # a float-noise-flat incumbent (e.g. a book sitting in cash) rides the
    # diffuse prior, so its 1%/day post_std must not buy it a retention
    # margin — review finding: it would otherwise lock champion ~200 days
    books = fleet_books()
    books["flat"] = make_book([0.0] * 30)
    for d in pick_dates(100):
        plain = allocate.thompson_pick(books, d)
        if plain["champion"] != "flat":
            gated = allocate.thompson_pick(books, d, incumbent="flat",
                                           hysteresis=1e9)
            assert gated["champion"] == plain["champion"]
            flat_row = next(r for r in gated["rows"]
                            if r["strategy"] == "flat")
            assert flat_row["diffuse"] and not flat_row["insufficient"]
            return
    pytest.fail("flat won every draw — fixture broken")


def test_incumbent_missing_from_books_is_ignored():
    plain = allocate.thompson_pick(fleet_books(), "2026-06-12")
    gated = allocate.thompson_pick(fleet_books(), "2026-06-12",
                                   incumbent="retired", hysteresis=1e9)
    assert gated["champion"] == plain["champion"]
    assert gated["incumbent"] == "retired"


def test_thompson_pick_with_incumbent_is_deterministic():
    a = allocate.thompson_pick(fleet_books(), "2026-06-12",
                               incumbent="mediocre", hysteresis=2.0)
    b = allocate.thompson_pick(fleet_books(), "2026-06-12",
                               incumbent="mediocre", hysteresis=2.0)
    assert a == b


def test_last_recorded_champion_latest_entry_strictly_before_pick_date(
        tmp_path):
    path = tmp_path / "allocator.json"
    path.write_text(json.dumps({"picks": [
        {"date": "2026-06-10", "champion": "a"},
        {"date": "2026-06-12", "champion": "c"},
        {"date": "2026-06-11", "champion": "b"},
    ]}))
    assert allocate.last_recorded_champion("2026-06-13", path) == "c"
    # a --force re-record of a date must not be its own incumbent
    assert allocate.last_recorded_champion("2026-06-12", path) == "b"
    assert allocate.last_recorded_champion("2026-06-10", path) is None


@pytest.mark.parametrize("content", [None, "", "{not json", "[]", "null",
                                     '{"picks": "abc"}', '{"picks": []}'])
def test_last_recorded_champion_missing_or_bad_history_is_none(tmp_path,
                                                               content):
    path = tmp_path / "allocator.json"
    if content is not None:
        path.write_text(content)
    assert allocate.last_recorded_champion("2026-06-12", path) is None


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


def test_main_pick_is_byte_identical_across_hash_seeds(tmp_path):
    # same-process reruns can't catch a regression to PYTHONHASHSEED-
    # sensitive seeding (e.g. random.Random(hash(name))); run the pick in
    # two subprocesses with different hash seeds and require byte-identical
    # output. Hermetic: subprocess of this interpreter, tmp_path state only.
    import os
    import subprocess
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    scripts = str(Path(allocate.__file__).parent)
    alloc = tmp_path / "allocator.json"
    code = (f"import sys; sys.path.insert(0, {scripts!r}); "
            f"from pathlib import Path; import allocate; "
            f"allocate.PAPER_PATH = Path({str(p)!r}); "
            f"allocate.ALLOC_PATH = Path({str(alloc)!r}); "
            f"sys.argv = ['allocate.py', '--pick', '--date', '2026-06-12', "
            f"'--json']; allocate.main()")
    outs = []
    for hash_seed in ("0", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed})
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert outs[0] == outs[1]


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


def test_main_pick_empty_books_json_emits_null_champion(tmp_path, monkeypatch,
                                                        capsys):
    # downstream consumers parse this shape: exit 0, champion null, rows []
    p = tmp_path / "paper.json"
    write_state(p, {})
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["champion"] is None
    assert out["rows"] == []


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
    assert ("--date/--seed/--hysteresis only apply with --pick"
            in capsys.readouterr().err)


def test_main_pick_json_reports_default_hysteresis_and_null_incumbent(
        tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["hysteresis"] == allocate.HYSTERESIS
    assert out["incumbent"] is None


def test_main_pick_reads_incumbent_from_allocator_history(tmp_path,
                                                          monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    (tmp_path / "allocator.json").write_text(json.dumps(
        {"picks": [{"date": "2026-06-11", "champion": "mediocre"}]}))
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json",
             "--hysteresis", "1e9")
    out = json.loads(capsys.readouterr().out)
    assert out["incumbent"] == "mediocre"
    assert out["hysteresis"] == 1e9
    # mediocre is scored (sufficient data): an absurd margin retains it
    assert out["champion"] == "mediocre"


def test_main_pick_hysteresis_zero_matches_plain_pick_despite_incumbent(
        tmp_path, monkeypatch, capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    (tmp_path / "allocator.json").write_text(json.dumps(
        {"picks": [{"date": "2026-06-11", "champion": "mediocre"}]}))
    run_main(monkeypatch, p, "--pick", "--date", "2026-06-12", "--json",
             "--hysteresis", "0")
    out = json.loads(capsys.readouterr().out)
    assert out["champion"] == allocate.thompson_pick(
        fleet_books(), "2026-06-12")["champion"]
    assert out["champion"] == out["rows"][0]["strategy"]


def test_main_hysteresis_without_pick_is_usage_error(tmp_path, monkeypatch,
                                                     capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p, "--hysteresis", "1")
    assert exc.value.code == 2
    assert ("--date/--seed/--hysteresis only apply with --pick"
            in capsys.readouterr().err)


def test_main_negative_hysteresis_is_usage_error(tmp_path, monkeypatch,
                                                 capsys):
    p = tmp_path / "paper.json"
    write_state(p, fleet_books())
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, p, "--pick", "--date", "2026-06-12",
                 "--hysteresis", "-1")
    assert exc.value.code == 2
    assert "--hysteresis must be >= 0" in capsys.readouterr().err
