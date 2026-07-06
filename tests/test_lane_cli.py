"""Tests for `trader lane ...` — run recording, artifacts, completion checks."""

import json

import pytest
from sqlalchemy import select

from trader import cli
from trader.db.models import LaneRun


@pytest.fixture()
def lane_cli(db_session, monkeypatch, tmp_path):
    """Run the real CLI against the in-memory schema, mirroring to tmp_path."""
    import trader.cli_cmds.lane as lane_mod
    import trader.db.session as session_mod

    monkeypatch.setattr(session_mod, "get_session", lambda url=None: db_session)
    monkeypatch.setattr(lane_mod, "ARTIFACT_ROOT", tmp_path / "artifacts")

    def run(argv, stdin: str | None = None):
        if stdin is not None:
            import io

            monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        return cli.main(argv)

    return run


def test_ping_ok(lane_cli, capsys):
    assert lane_cli(["lane", "ping"]) == 0
    assert "ok" in capsys.readouterr().out


def test_record_start_creates_running_row_and_account(lane_cli, db_session, capsys):
    assert lane_cli(["lane", "record-start", "research"]) == 0
    run_id = int(capsys.readouterr().out.strip())
    run = db_session.get(LaneRun, run_id)
    assert run.lane == "research"
    assert run.status == "running"
    assert run.finished_at is None
    assert run.account_id is not None


def test_record_end_completes_run(lane_cli, db_session, capsys):
    lane_cli(["lane", "record-start", "risk"])
    run_id = int(capsys.readouterr().out.strip())
    assert lane_cli(["lane", "record-end", str(run_id), "--status", "completed", "--summary", "3 verdicts"]) == 0
    run = db_session.get(LaneRun, run_id)
    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.summary == "3 verdicts"


def test_record_end_unknown_run_fails(lane_cli):
    assert lane_cli(["lane", "record-end", "9999", "--status", "failed"]) == 1


def test_artifact_put_get_roundtrip(lane_cli, db_session, capsys, tmp_path):
    lane_cli(["lane", "record-start", "research"])
    run_id = int(capsys.readouterr().out.strip())
    brief = {"date": "2026-07-06", "candidates": [{"symbol": "NVDA"}]}
    assert lane_cli(["lane", "artifact", "put", "research", "--run-id", str(run_id)], stdin=json.dumps(brief)) == 0
    capsys.readouterr()
    lane_cli(["lane", "record-end", str(run_id), "--status", "completed"])

    assert lane_cli(["lane", "artifact", "get", "research"]) == 0
    assert json.loads(capsys.readouterr().out) == brief

    mirrors = list((tmp_path / "artifacts").rglob("research.json"))
    assert len(mirrors) == 1
    assert json.loads(mirrors[0].read_text()) == brief


def test_artifact_put_defaults_to_latest_run(lane_cli, db_session, capsys):
    lane_cli(["lane", "record-start", "thesis"])
    run_id = int(capsys.readouterr().out.strip())
    assert lane_cli(["lane", "artifact", "put", "thesis"], stdin='{"theses": []}') == 0
    assert db_session.get(LaneRun, run_id).artifact == {"theses": []}


def test_artifact_put_rejects_bad_json(lane_cli, capsys):
    lane_cli(["lane", "record-start", "thesis"])
    capsys.readouterr()
    assert lane_cli(["lane", "artifact", "put", "thesis"], stdin="not json") == 1


def test_artifact_put_rejects_wrong_lane(lane_cli, capsys):
    lane_cli(["lane", "record-start", "thesis"])
    run_id = int(capsys.readouterr().out.strip())
    assert lane_cli(["lane", "artifact", "put", "risk", "--run-id", str(run_id)], stdin="{}") == 1


def test_artifact_get_missing_fails(lane_cli):
    assert lane_cli(["lane", "artifact", "get", "review"]) == 1


def test_check_requires_completed_run(lane_cli, db_session, capsys):
    assert lane_cli(["lane", "check", "execution"]) == 1
    lane_cli(["lane", "record-start", "execution"])
    run_id = int(capsys.readouterr().out.strip())
    # still running -> not complete
    assert lane_cli(["lane", "check", "execution"]) == 1
    lane_cli(["lane", "record-end", str(run_id), "--status", "failed"])
    assert lane_cli(["lane", "check", "execution"]) == 1
    lane_cli(["lane", "record-start", "execution"])
    run_id2 = int(capsys.readouterr().out.strip())
    lane_cli(["lane", "record-end", str(run_id2), "--status", "completed"])
    assert lane_cli(["lane", "check", "execution"]) == 0
    assert capsys.readouterr().out.strip() == str(run_id2)


def test_check_other_day_not_counted(lane_cli, db_session, capsys):
    lane_cli(["lane", "record-start", "review"])
    run_id = int(capsys.readouterr().out.strip())
    lane_cli(["lane", "record-end", str(run_id), "--status", "completed"])
    assert lane_cli(["lane", "check", "review", "--date", "2020-01-01"]) == 1
