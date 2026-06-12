"""Tests for allocator slice 3 — verdict persistence (allocate.py --record)
and the scoreboard's champion / pick-history view. Hermetic: no network,
tmp_path only."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import allocate  # noqa: E402
import scoreboard  # noqa: E402


def make_book(returns, start=10000.0):
    """Synthetic paper book whose history compounds the given daily returns."""
    history = [{"date": "d1", "value": round(start, 2)}]
    v = start
    for i, r in enumerate(returns, 2):
        v *= 1 + r
        history.append({"date": f"d{i}", "value": round(v, 2)})
    return {"cash": v, "starting_cash": start, "started": "d1",
            "position": None, "trades": [], "history": history}


def fleet_books():
    return {"dominant": make_book([0.006, 0.002] * 30),
            "mediocre": make_book([0.002, -0.002] * 30),
            "sparse": make_book([0.05] * 5)}


def write_paper(path, books, last_run="2026-06-12"):
    path.write_text(json.dumps({"last_run_date": last_run, "books": books}))


def run_allocate(monkeypatch, tmp_path, *argv, books=None):
    paper = tmp_path / "paper.json"
    if not paper.exists():
        write_paper(paper, fleet_books() if books is None else books)
    monkeypatch.setattr(allocate, "PAPER_PATH", paper)
    monkeypatch.setattr(allocate, "ALLOC_PATH", tmp_path / "allocator.json")
    monkeypatch.setattr(sys, "argv", ["allocate.py", *argv])
    allocate.main()


def read_history(tmp_path):
    return json.loads((tmp_path / "allocator.json").read_text())


# --- persistence ----------------------------------------------------------------

def test_record_persists_pick_with_date_champion_weights_scores(
        tmp_path, monkeypatch, capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12")
    hist = read_history(tmp_path)
    assert len(hist["picks"]) == 1
    entry = hist["picks"][0]
    assert entry["date"] == "2026-06-12"
    assert entry["champion"] in fleet_books()
    assert set(entry["weights"]) == set(fleet_books())
    assert set(entry["scores"]) == set(fleet_books())
    assert entry["scores"]["sparse"] is None  # insufficient: no fake score
    assert entry["scores"]["dominant"] is not None
    assert sum(entry["weights"].values()) == pytest.approx(1.0)
    out = capsys.readouterr().out
    assert "recorded pick for 2026-06-12" in out
    assert "champion today:" in out
    assert not (tmp_path / "allocator.json.tmp").exists()  # atomic, no debris


def test_record_matches_thompson_pick_for_the_date(tmp_path, monkeypatch,
                                                   capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12")
    capsys.readouterr()
    expected = allocate.thompson_pick(fleet_books(), "2026-06-12")
    entry = read_history(tmp_path)["picks"][0]
    assert entry["champion"] == expected["champion"]
    assert entry["weights"] == {r["strategy"]: round(r["weight"], 6)
                                for r in expected["rows"]}


def test_record_same_day_is_noop_that_says_so(tmp_path, monkeypatch, capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12")
    capsys.readouterr()
    before = (tmp_path / "allocator.json").read_text()
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12")
    out = capsys.readouterr().out
    assert "already recorded for 2026-06-12" in out
    assert "champion today:" in out  # verdict still journalable on a re-run
    assert (tmp_path / "allocator.json").read_text() == before  # no duplicate


def test_record_force_reevaluates_and_replaces(tmp_path, monkeypatch, capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12")
    capsys.readouterr()
    # a different seed offset changes the draws; without --force it must not
    # touch the stored entry, with --force it replaces it in place
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12", "--seed", "99")
    capsys.readouterr()
    assert read_history(tmp_path)["picks"][0]["seed"] == 0
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12", "--seed", "99", "--force")
    out = capsys.readouterr().out
    assert "replaced pick for 2026-06-12" in out
    hist = read_history(tmp_path)
    assert len(hist["picks"]) == 1
    assert hist["picks"][0]["seed"] == 99


def test_record_keeps_history_chronological(tmp_path, monkeypatch, capsys):
    for d in ("2026-06-12", "2026-06-10", "2026-06-11"):
        run_allocate(monkeypatch, tmp_path, "--pick", "--record", "--date", d)
    capsys.readouterr()
    dates = [p["date"] for p in read_history(tmp_path)["picks"]]
    assert dates == ["2026-06-10", "2026-06-11", "2026-06-12"]


def test_record_json_output_carries_record_status(tmp_path, monkeypatch,
                                                  capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["record"]["status"] == "recorded"
    assert out["record"]["verdict"].startswith("champion today: ")
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12", "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["record"]["status"] == "skipped"


def test_record_empty_books_records_nothing(tmp_path, monkeypatch, capsys):
    run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                 "--date", "2026-06-12", books={})
    out = capsys.readouterr().out
    assert "nothing to pick" in out
    assert "nothing recorded" in out
    assert not (tmp_path / "allocator.json").exists()


def test_record_missing_paper_state_exits_nonzero(tmp_path, monkeypatch,
                                                  capsys):
    monkeypatch.setattr(allocate, "PAPER_PATH", tmp_path / "paper.json")
    monkeypatch.setattr(allocate, "ALLOC_PATH", tmp_path / "allocator.json")
    monkeypatch.setattr(sys, "argv", ["allocate.py", "--pick", "--record",
                                      "--date", "2026-06-12"])
    with pytest.raises(SystemExit) as exc:
        allocate.main()
    assert exc.value.code == 1
    assert "no paper state" in capsys.readouterr().err
    assert not (tmp_path / "allocator.json").exists()


def test_record_corrupt_history_exits_nonzero(tmp_path, monkeypatch, capsys):
    (tmp_path / "allocator.json").write_text("{not json")
    with pytest.raises(SystemExit) as exc:
        run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                     "--date", "2026-06-12")
    assert exc.value.code == 1
    assert "unreadable allocator history" in capsys.readouterr().err


@pytest.mark.parametrize("content", [
    "[]",                              # valid JSON, not an object
    "null",                            # valid JSON, not an object
    '{"picks": "abc"}',                # picks is not a list
    '{"picks": [{"champion": "a"}]}',  # pick entry missing "date"
])
def test_record_wrong_shape_history_exits_nonzero(tmp_path, monkeypatch,
                                                  capsys, content):
    (tmp_path / "allocator.json").write_text(content)
    with pytest.raises(SystemExit) as exc:
        run_allocate(monkeypatch, tmp_path, "--pick", "--record",
                     "--date", "2026-06-12")
    assert exc.value.code == 1
    assert "unreadable allocator history" in capsys.readouterr().err
    # the malformed file is left exactly as found -- never overwritten
    assert (tmp_path / "allocator.json").read_text() == content


def test_record_without_pick_is_usage_error(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_allocate(monkeypatch, tmp_path, "--record")
    assert exc.value.code == 2
    assert "--record only applies with --pick" in capsys.readouterr().err


def test_force_without_record_is_usage_error(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_allocate(monkeypatch, tmp_path, "--pick", "--force",
                     "--date", "2026-06-12")
    assert exc.value.code == 2
    assert "--force only applies with --record" in capsys.readouterr().err


# --- pure helpers -----------------------------------------------------------------

def test_upsert_pick_statuses_and_order():
    picks = []
    e1 = {"date": "2026-06-12", "champion": "a"}
    assert allocate.upsert_pick(picks, e1) == "recorded"
    assert allocate.upsert_pick(picks, {"date": "2026-06-12",
                                        "champion": "b"}) == "skipped"
    assert picks == [e1]  # skip leaves the stored entry untouched
    assert allocate.upsert_pick(picks, {"date": "2026-06-12",
                                        "champion": "b"},
                                force=True) == "replaced"
    assert picks[0]["champion"] == "b"
    allocate.upsert_pick(picks, {"date": "2026-06-10", "champion": "c"})
    assert [p["date"] for p in picks] == ["2026-06-10", "2026-06-12"]


def test_verdict_line_formats_score_and_weight():
    entry = {"champion": "dominant",
             "weights": {"dominant": 0.5, "mediocre": 0.5},
             "scores": {"dominant": 4.567, "mediocre": None}}
    assert (allocate.verdict_line(entry)
            == "champion today: dominant (score 4.57, weight 0.5000)")


def test_verdict_line_insufficient_champion_says_na():
    entry = {"champion": "sparse", "weights": {"sparse": 1.0},
             "scores": {"sparse": None}}
    assert (allocate.verdict_line(entry)
            == "champion today: sparse (score n/a, weight 1.0000)")


# --- scoreboard -------------------------------------------------------------------

def alloc_picks(champions, start_day=1):
    return [{"date": f"2026-06-{start_day + i:02d}", "seed": 0,
             "half_life_days": 63.0, "champion": c,
             "weights": {c: 0.5}, "scores": {c: 1.23}}
            for i, c in enumerate(champions)]


def run_scoreboard(monkeypatch, tmp_path, *argv, picks=None):
    paper = tmp_path / "paper.json"
    if not paper.exists():
        write_paper(paper, fleet_books())
    if picks is not None:
        (tmp_path / "allocator.json").write_text(
            json.dumps({"picks": picks}))
    monkeypatch.setattr(scoreboard, "PAPER_PATH", paper)
    monkeypatch.setattr(scoreboard, "ALLOC_PATH", tmp_path / "allocator.json")
    monkeypatch.setattr(sys, "argv", ["scoreboard.py", *argv])
    scoreboard.main()


def test_scoreboard_shows_champion_and_recent_picks(tmp_path, monkeypatch,
                                                    capsys):
    picks = alloc_picks(["dominant", "dominant", "mediocre"])
    run_scoreboard(monkeypatch, tmp_path, picks=picks)
    out = capsys.readouterr().out
    assert ("allocator champion (recommend-only): mediocre "
            "(score 1.23, weight 0.5000) as of 2026-06-03") in out
    assert "recent picks (last 3):" in out
    assert "2026-06-03  mediocre  <- switch" in out
    assert "2026-06-02  dominant\n" in out  # no marker when unchanged


def test_scoreboard_limits_history_and_marks_switch_across_window(
        tmp_path, monkeypatch, capsys):
    # 12 picks; the window shows the last 10 and the first windowed pick
    # still gets a switch marker computed against the pick just before it
    picks = alloc_picks(["a"] * 2 + ["b"] + ["a"] * 9)
    run_scoreboard(monkeypatch, tmp_path, picks=picks)
    out = capsys.readouterr().out
    assert "recent picks (last 10):" in out
    assert "2026-06-01" not in out and "2026-06-02" not in out
    assert "2026-06-03  b  <- switch" in out
    assert "2026-06-04  a  <- switch" in out


def test_scoreboard_without_allocator_state_degrades_gracefully(
        tmp_path, monkeypatch, capsys):
    run_scoreboard(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "paper fleet as of" in out
    assert "allocator champion" not in out


def test_scoreboard_with_corrupt_allocator_state_degrades_gracefully(
        tmp_path, monkeypatch, capsys):
    (tmp_path / "allocator.json").write_text("{not json")
    run_scoreboard(monkeypatch, tmp_path)
    out = capsys.readouterr().out
    assert "paper fleet as of" in out
    assert "allocator champion" not in out


def test_scoreboard_json_includes_allocator_summary(tmp_path, monkeypatch,
                                                    capsys):
    picks = alloc_picks(["dominant"] * 12)
    run_scoreboard(monkeypatch, tmp_path, "--json", picks=picks)
    out = json.loads(capsys.readouterr().out)
    assert out["allocator"]["champion"] == "dominant"
    assert len(out["allocator"]["picks"]) == 10  # last 10 only


def test_scoreboard_json_allocator_null_when_missing(tmp_path, monkeypatch,
                                                     capsys):
    run_scoreboard(monkeypatch, tmp_path, "--json")
    out = json.loads(capsys.readouterr().out)
    assert out["allocator"] is None
