import pytest

from trader import envelope, params


def test_defaults_all_validate():
    for name, value in params.defaults().items():
        assert params.validate(name, value) == value


def test_reject_out_of_envelope():
    with pytest.raises(params.EnvelopeViolation):
        params.validate("options_sleeve_budget_fraction", 0.50)
    with pytest.raises(params.EnvelopeViolation):
        params.validate("per_position_max_fraction", 0.01)
    with pytest.raises(params.EnvelopeViolation):
        params.validate("max_trades_per_day", 7)


def test_reject_unknown_param():
    with pytest.raises(params.EnvelopeViolation):
        params.validate("account_kill_switch_drawdown", 0.10)


def test_reject_non_integer_for_count_params():
    with pytest.raises(params.EnvelopeViolation):
        params.validate("max_concurrent_positions", 4.5)


def test_clamp_mode():
    assert params.validate("options_sleeve_budget_fraction", 0.50, clamp=True) == pytest.approx(
        envelope.OPTIONS_SLEEVE_BUDGET_MAX
    )
    assert params.validate("max_concurrent_positions", 100, clamp=True) == envelope.CONCURRENT_POSITIONS_MAX
    assert params.validate("max_concurrent_positions", 4.4, clamp=True) == 4


def test_current_without_session_returns_defaults():
    assert params.current(None) == params.defaults()


def test_current_reads_latest_param_history(db_session):
    from trader.db.models import Account, ParamHistory

    account = Account(name="test")
    db_session.add(account)
    db_session.flush()
    db_session.add(
        ParamHistory(
            account_id=account.id,
            param_name="max_trades_per_day",
            old_value="3",
            new_value="5",
            evidence="test",
            actor="human",
        )
    )
    db_session.commit()

    values = params.current(db_session)
    assert values["max_trades_per_day"] == 5
    assert values["per_position_max_fraction"] == envelope.PER_POSITION_DEFAULT
