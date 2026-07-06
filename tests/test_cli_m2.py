"""CLI coverage for the M2 commands (sleeves, kill-switch, dry-run, quotes,
reconcile) against an in-memory SQLite database."""

import json

import pytest
from sqlalchemy.orm import sessionmaker

from trader import cli


@pytest.fixture(autouse=True)
def _wire_db(db_session, monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("trader.db.session.get_session", lambda url=None: factory())


def test_help_lists_m2_commands(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    for cmd in ("sleeves", "kill-switch", "dry-run", "quotes", "reconcile"):
        assert cmd in out


def test_sleeves_init_and_status(capsys):
    assert cli.main(["sleeves", "init"]) == 0
    out = capsys.readouterr().out
    assert "equity" in out and "options" in out

    assert cli.main(["kill-switch", "update", "--equity", "100000"]) == 0
    capsys.readouterr()

    assert cli.main(["sleeves", "status"]) == 0
    out = capsys.readouterr().out
    assert "budget 75%" in out
    assert "budget 25%" in out
    assert "$100,000.00" in out


def test_sleeves_status_without_init_fails(capsys):
    assert cli.main(["sleeves", "status"]) == 1


def test_kill_switch_status_and_trip(capsys):
    cli.main(["sleeves", "init"])
    cli.main(["kill-switch", "update", "--equity", "100000"])
    capsys.readouterr()

    assert cli.main(["kill-switch", "status"]) == 0
    out = capsys.readouterr().out
    assert "ok" in out

    # 35% drawdown trips the fixed account kill-switch; exit becomes 1.
    assert cli.main(["kill-switch", "update", "--equity", "65000"]) == 1
    out = capsys.readouterr().out
    assert "HALTED" in out
    assert cli.main(["kill-switch", "status"]) == 1


def test_kill_switch_update_with_sleeve_values(capsys):
    cli.main(["sleeves", "init"])
    assert (
        cli.main([
            "kill-switch", "update", "--equity", "100000",
            "--equity-sleeve", "75000", "--options-sleeve", "25000",
        ])
        == 0
    )
    out = capsys.readouterr().out
    assert "sleeve equity" in out and "sleeve options" in out


def test_dry_run_flip(capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    assert cli.main(["dry-run", "status"]) == 0
    assert "ON" in capsys.readouterr().out

    assert cli.main(["dry-run", "off", "--reason", "M5 go-live"]) == 0
    capsys.readouterr()
    assert cli.main(["dry-run", "status"]) == 0
    assert "OFF" in capsys.readouterr().out

    assert cli.main(["dry-run", "on"]) == 0
    capsys.readouterr()
    assert cli.main(["dry-run", "status"]) == 0
    assert "ON" in capsys.readouterr().out


def test_quotes_record_from_file(tmp_path, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    quote_file = tmp_path / "quotes.json"
    quote_file.write_text(json.dumps([
        {"symbol": "NVDA", "kind": "equity", "price": 190.5, "avg_dollar_volume": 3.2e10},
        {"symbol": "NVDA", "kind": "option", "occ_symbol": "NVDA260807C00200000",
         "bid": 4.9, "ask": 5.1, "open_interest": 1200},
    ]))
    assert cli.main(["quotes", "record", "--file", str(quote_file)]) == 0
    assert "recorded 2 quote(s)" in capsys.readouterr().out


def test_reconcile_flags_unauthorized_with_nonzero_exit(tmp_path, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    broker_file = tmp_path / "broker.json"
    broker_file.write_text(json.dumps([
        {"ref_id": "rogue-1", "state": "filled",
         "executions": [{"quantity": 1, "price": 50.0}]},
    ]))
    assert cli.main(["reconcile", "--file", str(broker_file)]) == 1
    captured = capsys.readouterr()
    assert "UNAUTHORIZED" in captured.err
    assert "FLAGGED" in captured.err


def test_reconcile_clean_exit_zero(tmp_path, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    broker_file = tmp_path / "broker.json"
    broker_file.write_text("[]")
    assert cli.main(["reconcile", "--file", str(broker_file)]) == 0
    assert "clean" in capsys.readouterr().out
