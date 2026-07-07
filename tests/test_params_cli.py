"""`trader params set` and `trader ramp` — envelope enforcement and history."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from trader import cli, envelope
from trader.db.models import ParamHistory, Sleeve


@pytest.fixture(autouse=True)
def _wire_db(db_session, monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    monkeypatch.setattr("trader.db.session.get_session", lambda url=None: factory())


def _history(db_session, name):
    return (
        db_session.execute(
            select(ParamHistory)
            .where(ParamHistory.param_name == name)
            .order_by(ParamHistory.id)
        )
        .scalars()
        .all()
    )


def test_params_set_writes_history(db_session, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    assert cli.main([
        "params", "set", "max_trades_per_day", "5", "--evidence", "test evidence"
    ]) == 0
    assert "max_trades_per_day: 3 -> 5" in capsys.readouterr().out

    rows = _history(db_session, "max_trades_per_day")
    assert len(rows) == 1
    assert rows[0].old_value == "3"
    assert rows[0].new_value == "5"
    assert rows[0].evidence == "test evidence"
    assert rows[0].actor == "improve"

    from trader import params as params_mod

    assert params_mod.current(db_session)["max_trades_per_day"] == 5


def test_params_set_rejects_out_of_envelope(db_session, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    assert cli.main([
        "params", "set", "max_trades_per_day", "7", "--evidence", "greed"
    ]) == 1
    assert "ENVELOPE REJECTED" in capsys.readouterr().err
    assert _history(db_session, "max_trades_per_day") == []

    assert cli.main([
        "params", "set", "per_position_max_fraction", "0.10", "--evidence", "greed"
    ]) == 1
    assert _history(db_session, "per_position_max_fraction") == []


def test_params_set_rejects_unknown_param(capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()
    with pytest.raises(SystemExit):  # argparse choices reject before the DB
        cli.main(["params", "set", "account_kill_switch_drawdown", "0.1", "--evidence", "x"])


def test_params_set_requires_account(capsys):
    assert cli.main([
        "params", "set", "max_trades_per_day", "4", "--evidence", "x"
    ]) == 1
    assert "no account row" in capsys.readouterr().err


def test_params_set_actor_override(db_session, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()
    assert cli.main([
        "params", "set", "max_trades_per_day", "2",
        "--evidence", "human ramp", "--actor", "human",
    ]) == 0
    assert _history(db_session, "max_trades_per_day")[0].actor == "human"


def _options_sleeve(db_session):
    return db_session.execute(
        select(Sleeve).where(Sleeve.type == "options")
    ).scalar_one()


def test_ramp_start_half_caps_and_options_halt(db_session, capsys):
    cli.main(["sleeves", "init"])
    capsys.readouterr()

    assert cli.main(["ramp", "start"]) == 0
    out = capsys.readouterr().out
    assert "half caps" in out

    from trader import params as params_mod

    current = params_mod.current(db_session)
    assert current["per_position_max_fraction"] == 0.025
    assert current["max_concurrent_positions"] == 3
    db_session.expire_all()
    assert _options_sleeve(db_session).halted is True
    # Recorded with evidence + human actor.
    rows = _history(db_session, "per_position_max_fraction")
    assert rows and rows[-1].actor == "human" and "ramp" in rows[-1].evidence


def test_ramp_options_on_clears_halt(db_session, capsys):
    cli.main(["sleeves", "init"])
    cli.main(["ramp", "start"])
    capsys.readouterr()

    assert cli.main(["ramp", "options-on"]) == 0
    db_session.expire_all()
    assert _options_sleeve(db_session).halted is False


def test_ramp_full_restores_defaults(db_session, capsys):
    cli.main(["sleeves", "init"])
    cli.main(["ramp", "start"])
    capsys.readouterr()

    assert cli.main(["ramp", "full"]) == 0
    from trader import params as params_mod

    current = params_mod.current(db_session)
    assert current["per_position_max_fraction"] == envelope.PER_POSITION_DEFAULT
    assert current["max_concurrent_positions"] == envelope.CONCURRENT_POSITIONS_DEFAULT
    db_session.expire_all()
    assert _options_sleeve(db_session).halted is False


def test_ramp_requires_account(capsys):
    assert cli.main(["ramp", "start"]) == 1
    assert "no account row" in capsys.readouterr().err
