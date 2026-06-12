"""Tests for scripts/replay_allocator.py — the historical allocator replay.

Hermetic: synthetic per-strategy book histories only (offline --books mode
and the pure replay() function); no network, tmp_path only. The core
guarantee under test is NO LOOKAHEAD: the champion traded on day t must be
chosen from book values dated <= t-1, so a one-day +500% spike injected
into a book can never be captured by the meta-portfolio on the spike day
unless that book was already champion on prior data. The spike is huge and
the no-lookahead tests sweep several spike positions, so a genuine one-day
lookahead (truncating to <= t instead of <= t-1) flips the Thompson
champion to the spiked book deterministically — mutation-tested: that exact
mutant fails these tests, independent of any single date-seeded RNG draw.
"""
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import allocate  # noqa: E402
import replay_allocator  # noqa: E402


def iso_days(n, start="2025-01-01"):
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def hist_from_returns(returns, dates, start=10000.0):
    """Book history compounding the given daily returns over dates."""
    out = [{"date": dates[0], "value": start}]
    v = start
    for r, d in zip(returns, dates[1:]):
        v *= 1 + r
        out.append({"date": d, "value": v})
    return out


N_DAYS = 80
DATES = iso_days(N_DAYS)
ALPHA_RETS = [0.006, 0.002] * ((N_DAYS - 1) // 2 + 1)  # mean +0.4%/day
BETA_RETS = [-0.002, -0.006] * ((N_DAYS - 1) // 2 + 1)  # mean -0.4%/day


def make_books(spike_at=None, spike=5.0):
    """Dominant alpha vs poor beta; optionally a one-day +spike return is
    injected into beta at index spike_at (the jump persists in its values).
    The default spike (+500%) is big enough that any implementation with a
    one-day lookahead flips the Thompson champion to beta on the spike day,
    whatever the date-seeded gaussian draws happen to be."""
    beta = BETA_RETS[:N_DAYS - 1].copy()
    if spike_at is not None:
        beta[spike_at - 1] += spike  # return *into* DATES[spike_at]
    return {
        "alpha": {"history": hist_from_returns(ALPHA_RETS[:N_DAYS - 1], DATES)},
        "beta": {"history": hist_from_returns(beta, DATES)},
    }


# --- replay mechanics -----------------------------------------------------------

def test_replay_picks_daily_after_warmup():
    result = replay_allocator.replay(make_books())
    w = replay_allocator.WARMUP_DAYS
    assert len(result["picks"]) == N_DAYS - w
    assert [p["date"] for p in result["picks"]] == DATES[w:]
    assert result["start"] == DATES[w]
    assert result["end"] == DATES[-1]
    assert result["meta_history"][0] == {"date": DATES[w - 1],
                                         "value": replay_allocator.META_START}


def test_replay_pick_matches_allocate_scheme_on_truncated_data():
    # the pick for day t must equal allocate.thompson_pick over histories
    # truncated to <= t-1, RNG-keyed by day t — the slice-2 scheme exactly,
    # with yesterday's champion threaded as the hysteresis incumbent (None
    # for the first pick after warmup)
    books = make_books()
    result = replay_allocator.replay(books)
    by_date = {p["date"]: p["champion"] for p in result["picks"]}
    for idx in (replay_allocator.WARMUP_DAYS, 40, N_DAYS - 1):
        day = DATES[idx]
        trunc = {n: {"history": [h for h in b["history"]
                                 if h["date"] <= DATES[idx - 1]]}
                 for n, b in books.items()}
        expected = allocate.thompson_pick(
            trunc, day, incumbent=by_date.get(DATES[idx - 1]))["champion"]
        assert by_date[day] == expected


def test_replay_hysteresis_zero_matches_plain_slice2_picks():
    # --hysteresis 0 must reproduce the original incumbent-free replay
    books = make_books()
    result = replay_allocator.replay(books, hysteresis=0.0)
    assert result["hysteresis"] == 0.0
    for idx in (replay_allocator.WARMUP_DAYS, 30, 55, N_DAYS - 1):
        day = DATES[idx]
        trunc = {n: {"history": [h for h in b["history"]
                                 if h["date"] <= DATES[idx - 1]]}
                 for n, b in books.items()}
        expected = allocate.thompson_pick(trunc, day)["champion"]
        got = next(p["champion"] for p in result["picks"] if p["date"] == day)
        assert got == expected


def noisy_books(n_strats=4, n_days=160, seed=7):
    """Several similar noisy books — leadership is ambiguous, so the plain
    h=0 allocator switches often; raising hysteresis must damp that."""
    rng = random.Random(seed)
    dates = iso_days(n_days)
    return {f"s{k}": {"history": hist_from_returns(
                [rng.gauss(0.0005, 0.01) for _ in range(n_days - 1)], dates)}
            for k in range(n_strats)}


def test_replay_switches_decrease_with_hysteresis_without_freezing():
    books = noisy_books()
    sw = {h: replay_allocator.replay(books, hysteresis=h)["switches"]
          for h in (0.0, 1.0, 3.0)}
    assert sw[0.0] > 0                      # fixture actually switches at h=0
    assert sw[0.0] >= sw[1.0] >= sw[3.0]    # monotonic-ish damping
    assert sw[3.0] < sw[0.0]                # strictly fewer at high margin
    # sanity: hysteresis damps switching, it must not freeze the champion
    # forever on an ambiguous-leadership fixture
    assert sw[1.0] > 0


def test_replay_dominant_strategy_is_held_and_meta_compounds_it():
    result = replay_allocator.replay(make_books())
    champs = {p["champion"] for p in result["picks"]}
    assert "alpha" in champs
    # meta must end well above start: alpha compounds ~+0.4%/day
    assert result["meta_history"][-1]["value"] > replay_allocator.META_START


def test_replay_segments_cover_picks_and_switch_count_consistent():
    result = replay_allocator.replay(make_books())
    segs = result["segments"]
    assert sum(s["days"] for s in segs) == len(result["picks"])
    assert result["switches"] == len(segs) - 1
    for a, b in zip(segs, segs[1:]):
        assert a["champion"] != b["champion"]
        assert a["end"] < b["start"]


def test_replay_equal_weight_is_mean_of_daily_returns():
    result = replay_allocator.replay(make_books())
    w = replay_allocator.WARMUP_DAYS
    expected = replay_allocator.META_START
    for ra, rb in zip(ALPHA_RETS[w - 1:N_DAYS - 1], BETA_RETS[w - 1:N_DAYS - 1]):
        expected *= 1 + (ra + rb) / 2
    assert result["ew_history"][-1]["value"] == pytest.approx(expected)


def test_replay_window_start_end_bounds_calendar():
    result = replay_allocator.replay(make_books(), start=DATES[10],
                                     end=DATES[70])
    w = replay_allocator.WARMUP_DAYS
    assert result["meta_history"][0]["date"] == DATES[10 + w - 1]
    assert result["end"] == DATES[70]


def test_replay_too_few_days_raises():
    with pytest.raises(ValueError, match="--warmup"):
        replay_allocator.replay(make_books(), warmup=N_DAYS)


def test_hold_books_are_never_candidates():
    books = make_books()
    # a buy-and-hold baseline that crushes everything must stay a baseline
    books["hold_spy"] = {"history": hist_from_returns(
        [0.05] * (N_DAYS - 1), DATES)}
    result = replay_allocator.replay(books)
    assert all(p["champion"] != "hold_spy" for p in result["picks"])
    rows = replay_allocator.comparison_rows(books, result)
    assert "hold_spy" in rows
    assert "best_single (alpha)" in rows  # hold_spy excluded from best single


# --- NO LOOKAHEAD ---------------------------------------------------------------

# several positions, so the guarantee never hinges on one date-seeded RNG
# draw — at every one, a one-day-lookahead mutant deterministically picks
# beta on the spike day (verified by mutation testing) while the correct
# <= t-1 truncation keeps alpha champion
SPIKE_POSITIONS = (25, 40, 60, 75)


@pytest.mark.parametrize("spike_at", SPIKE_POSITIONS)
def test_no_lookahead_spike_day_pick_uses_only_prior_data(spike_at):
    spiked = make_books(spike_at=spike_at)
    result = replay_allocator.replay(spiked)
    spike_date = DATES[spike_at]
    # independently recompute the pick from data strictly before the spike,
    # with the prior day's replay champion as the hysteresis incumbent —
    # the incumbent is itself prior information (picked on data <= t-2)
    trunc = {n: {"history": [h for h in b["history"]
                             if h["date"] <= DATES[spike_at - 1]]}
             for n, b in spiked.items()}
    incumbent = next(p["champion"] for p in result["picks"]
                     if p["date"] == DATES[spike_at - 1])
    expected = allocate.thompson_pick(trunc, spike_date,
                                      incumbent=incumbent)["champion"]
    got = next(p["champion"] for p in result["picks"]
               if p["date"] == spike_date)
    assert got == expected
    # on prior data beta is ~poor vs dominant alpha: it cannot be champion
    assert got == "alpha"


@pytest.mark.parametrize("spike_at", SPIKE_POSITIONS)
def test_no_lookahead_meta_cannot_capture_the_spike_day(spike_at):
    result = replay_allocator.replay(make_books(spike_at=spike_at))
    meta = result["meta_history"]
    i = next(i for i, h in enumerate(meta) if h["date"] == DATES[spike_at])
    day_ret = meta[i]["value"] / meta[i - 1]["value"] - 1
    # champion on the spike day was alpha (chosen without the spike), so the
    # meta-return is alpha's small daily return — nowhere near +500%
    assert day_ret == pytest.approx(ALPHA_RETS[spike_at - 1])
    assert day_ret < 0.05


@pytest.mark.parametrize("spike_at", SPIKE_POSITIONS)
def test_no_lookahead_picks_identical_with_and_without_spike_through_spike_day(
        spike_at):
    # data through t-1 is identical for every pick up to and including the
    # spike day, so the pick sequence must be too — the spike can only ever
    # influence picks from the *next* day on
    with_spike = replay_allocator.replay(make_books(spike_at=spike_at))
    without = replay_allocator.replay(make_books())
    cut = spike_at - replay_allocator.WARMUP_DAYS + 1
    assert with_spike["picks"][:cut] == without["picks"][:cut]
    assert with_spike["picks"][cut - 1]["date"] == DATES[spike_at]


# --- comparison rows --------------------------------------------------------------

def test_comparison_rows_have_required_entries_and_hindsight_ordering():
    books = make_books()
    books["hold_spy"] = {"history": hist_from_returns(
        [0.001] * (N_DAYS - 1), DATES)}
    result = replay_allocator.replay(books)
    rows = replay_allocator.comparison_rows(books, result)
    assert list(rows)[:1] == ["meta"]
    assert "best_single (alpha)" in rows
    assert "worst_single (beta)" in rows
    assert "equal_weight" in rows
    assert "hold_spy" in rows
    for r in rows.values():
        for key in ("years", "cagr", "sharpe", "max_dd", "final"):
            assert key in r
    assert rows["best_single (alpha)"]["final"] > rows["worst_single (beta)"]["final"]


def test_meta_tracks_dominant_strategy_when_evidence_is_clear():
    # with one clearly dominant book the meta should land near the best
    # single strategy and far above the worst — the value-add evidence
    result = replay_allocator.replay(make_books())
    rows = replay_allocator.comparison_rows(make_books(), result)
    assert rows["meta"]["cagr"] > rows["worst_single (beta)"]["cagr"]
    assert rows["meta"]["cagr"] > rows["equal_weight"]["cagr"]


# --- offline books loading ---------------------------------------------------------

def test_load_books_paper_style_and_flat(tmp_path):
    books = make_books()
    p1 = tmp_path / "paper_style.json"
    p1.write_text(json.dumps({"books": books}))
    p2 = tmp_path / "flat.json"
    p2.write_text(json.dumps({n: b["history"] for n, b in books.items()}))
    b1 = replay_allocator.load_books(p1)
    b2 = replay_allocator.load_books(p2)
    assert b1.keys() == b2.keys() == books.keys()
    assert b1["alpha"]["history"] == b2["alpha"]["history"]


def test_load_books_rejects_non_object(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("[1, 2, 3]")
    with pytest.raises(ValueError):
        replay_allocator.load_books(p)


# --- CLI -----------------------------------------------------------------------

def write_books(tmp_path, books=None):
    p = tmp_path / "books.json"
    p.write_text(json.dumps({"books": books or make_books()}))
    return p


def run_main(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["replay_allocator.py", *argv])
    replay_allocator.main()


def test_main_json_is_byte_identical_across_reruns(tmp_path, monkeypatch,
                                                   capsys):
    p = write_books(tmp_path)
    run_main(monkeypatch, "--books", str(p), "--json")
    first = capsys.readouterr().out
    run_main(monkeypatch, "--books", str(p), "--json")
    assert capsys.readouterr().out == first
    out = json.loads(first)
    assert out["mode"] == "books"
    assert out["cadence"] == "daily"
    assert out["seed"] == 0
    assert out["half_life"] == allocate.HALF_LIFE_DAYS
    assert out["picks"] == N_DAYS - replay_allocator.WARMUP_DAYS
    assert out["switches"] == len(out["segments"]) - 1
    assert "meta" in out["rows"] and "equal_weight" in out["rows"]
    assert "Black-Scholes" in out["caveat"]
    assert sum(out["champion_days"].values()) == out["picks"]


def test_main_json_is_byte_identical_across_hash_seeds(tmp_path):
    # the date-seeded scheme must not depend on PYTHONHASHSEED; run the
    # replay in two subprocesses with different hash seeds and require
    # byte-identical --json. Hermetic: subprocess of this interpreter.
    import os
    import subprocess
    p = write_books(tmp_path)
    scripts = str(Path(replay_allocator.__file__).parent)
    code = (f"import sys; sys.path.insert(0, {scripts!r}); "
            f"import replay_allocator; "
            f"sys.argv = ['replay_allocator.py', '--books', {str(p)!r}, "
            f"'--json']; replay_allocator.main()")
    outs = []
    for hash_seed in ("0", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True,
            env={**os.environ, "PYTHONHASHSEED": hash_seed})
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout)
    assert outs[0] == outs[1]


def test_main_seed_passthrough_changes_json(tmp_path, monkeypatch, capsys):
    p = write_books(tmp_path)
    run_main(monkeypatch, "--books", str(p), "--json", "--seed", "7")
    out = json.loads(capsys.readouterr().out)
    assert out["seed"] == 7


def test_main_hysteresis_passthrough_and_default(tmp_path, monkeypatch,
                                                 capsys):
    p = write_books(tmp_path)
    run_main(monkeypatch, "--books", str(p), "--json")
    assert (json.loads(capsys.readouterr().out)["hysteresis"]
            == allocate.HYSTERESIS)
    run_main(monkeypatch, "--books", str(p), "--json", "--hysteresis", "1.5")
    assert json.loads(capsys.readouterr().out)["hysteresis"] == 1.5


def test_main_table_output_shape(tmp_path, monkeypatch, capsys):
    p = write_books(tmp_path)
    run_main(monkeypatch, "--books", str(p))
    out = capsys.readouterr().out
    assert "allocator replay" in out
    assert "champion switches:" in out
    assert "champion timeline:" in out
    assert "meta" in out
    assert "best_single (alpha)" in out
    assert "worst_single (beta)" in out
    assert "equal_weight" in out
    assert "Black-Scholes" in out  # inherited options caveat


def test_main_start_end_window(tmp_path, monkeypatch, capsys):
    p = write_books(tmp_path)
    run_main(monkeypatch, "--books", str(p), "--json",
             "--start", DATES[10], "--end", DATES[70])
    out = json.loads(capsys.readouterr().out)
    assert out["start"] == DATES[10 + replay_allocator.WARMUP_DAYS]
    assert out["end"] == DATES[70]


def test_main_missing_books_file_exits_nonzero(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--books", str(tmp_path / "nope.json"))
    assert exc.value.code == 1
    assert "no books file" in capsys.readouterr().err


def test_main_corrupt_books_file_exits_nonzero(tmp_path, monkeypatch, capsys):
    p = tmp_path / "books.json"
    p.write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--books", str(p))
    assert exc.value.code == 1
    assert "unreadable books file" in capsys.readouterr().err


def test_main_not_enough_history_exits_nonzero(tmp_path, monkeypatch, capsys):
    p = write_books(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--books", str(p), "--warmup", str(N_DAYS))
    assert exc.value.code == 1
    assert "--warmup" in capsys.readouterr().err


@pytest.mark.parametrize("argv", [("--half-life", "0"),
                                  ("--half-life", "-5"),
                                  ("--warmup", "0"),
                                  ("--hysteresis", "-1"),
                                  ("--start", "20250101"),
                                  ("--end", "2025-13-01")])
def test_main_bad_flags_are_usage_errors(tmp_path, monkeypatch, capsys, argv):
    p = write_books(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run_main(monkeypatch, "--books", str(p), *argv)
    assert exc.value.code == 2
