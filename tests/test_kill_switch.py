import pytest

from trader.gates import kill_switch

from tests.gate_helpers import make_account


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")


def test_unknown_state_fails_closed(db_session):
    st = kill_switch.status(db_session)
    assert st.account_halted
    assert "no account" in st.reason

    make_account(db_session, equity=None)  # account exists, equity never fed
    st = kill_switch.status(db_session)
    assert st.account_halted
    assert "unknown" in st.reason


def test_update_sets_equity_and_ratchets_hwm(db_session):
    make_account(db_session, equity=None)
    st = kill_switch.update(db_session, 100_000)
    assert st.equity == 100_000 and st.hwm == 100_000
    assert not st.account_halted

    st = kill_switch.update(db_session, 120_000)
    assert st.hwm == 120_000

    st = kill_switch.update(db_session, 90_000)  # 25% down from 120k
    assert st.hwm == 120_000  # never comes down
    assert st.drawdown == pytest.approx(0.25)
    assert not st.account_halted


def test_account_kill_switch_trips_at_30_percent(db_session):
    make_account(db_session, equity=100_000)
    st = kill_switch.update(db_session, 70_000)  # exactly 30%
    assert st.account_halted
    assert "30%" in st.reason

    halted, reason = kill_switch.account_halted(db_session)
    assert halted


def test_account_survives_29_percent(db_session):
    make_account(db_session, equity=100_000)
    st = kill_switch.update(db_session, 71_000)
    assert not st.account_halted


def test_sleeve_halt_latches(db_session):
    account = make_account(db_session, equity=100_000)
    # equity sleeve HWM 75k, then drops 20% (default halt fraction is 15%)
    kill_switch.update(db_session, 100_000, {"equity": 75_000})
    st = kill_switch.update(db_session, 95_000, {"equity": 60_000})
    eq = next(s for s in st.sleeves if s.type == "equity")
    assert eq.halted
    assert eq.drawdown == pytest.approx(0.20)

    # Recovery does NOT auto-unhalt: the flag latched.
    st = kill_switch.update(db_session, 100_000, {"equity": 76_000})
    eq = next(s for s in st.sleeves if s.type == "equity")
    assert eq.halted
    assert "latched" in eq.reason

    sleeve = next(s for s in account.sleeves if s.type == "equity")
    halted, reason = kill_switch.sleeve_halted(db_session, sleeve)
    assert halted


def test_sleeve_below_halt_fraction_ok(db_session):
    account = make_account(db_session, equity=100_000)
    kill_switch.update(db_session, 100_000, {"equity": 75_000, "options": 25_000})
    st = kill_switch.update(db_session, 98_000, {"equity": 70_000, "options": 24_000})
    assert not any(s.halted for s in st.sleeves)
    for sleeve in account.sleeves:
        assert kill_switch.sleeve_halted(db_session, sleeve) == (False, "ok")


def test_update_rejects_bad_equity(db_session):
    make_account(db_session, equity=None)
    with pytest.raises(ValueError):
        kill_switch.update(db_session, 0)
    with pytest.raises(ValueError):
        kill_switch.update(db_session, -5)
