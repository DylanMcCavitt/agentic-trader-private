from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from trader.db.models import Order, ParamHistory
from trader.gates import option_gate
from trader.sleeves import ledger

from tests.gate_helpers import (
    NOW,
    add_filled_position,
    disable_dry_run,
    fresh_option_quote,
    make_account,
    option_order,
)

# OCC symbol for the default helper order: NVDA 2026-08-07 200C
OCC = "NVDA260807C00200000"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")


def setup_live_account(session, equity=100_000.0):
    account = make_account(session, equity=equity)
    disable_dry_run(session, account)
    fresh_option_quote(session, account, OCC)
    return account


def get_order(session, ref_id):
    return session.execute(select(Order).where(Order.ref_id == ref_id)).scalar_one_or_none()


# --- allow paths -----------------------------------------------------------


def test_clean_call_buy_allowed_and_recorded(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "allow", verdict.reason

    row = get_order(db_session, "opt-0001")
    assert row.status == "pending"
    assert row.instrument == "option"
    assert row.notional == Decimal("500")  # 1 contract x $5 x 100
    assert row.payload["position_key"] == OCC


def test_put_buy_allowed(db_session):
    account = setup_live_account(db_session)
    occ_put = "NVDA260807P00180000"
    fresh_option_quote(db_session, account, occ_put)
    verdict = option_gate.evaluate(
        db_session,
        option_order(ref_id="opt-put", option_type="put", strike_price=180.0),
        now=NOW,
    )
    assert verdict.decision == "allow", verdict.reason


def test_sell_to_close_allowed_with_position(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=2, price=4.0,
        instrument="option", position_key=OCC,
    )
    verdict = option_gate.evaluate(
        db_session,
        option_order(side="sell", position_effect="close", quantity=2),
        now=NOW,
    )
    assert verdict.decision == "allow", verdict.reason


# --- structure denies ------------------------------------------------------


def test_sell_to_open_denied(db_session):
    setup_live_account(db_session)
    for payload in (
        option_order(side="sell", position_effect="open"),
        option_order(side="sell_to_open", position_effect=None),
        option_order(side="sell", position_effect=None),  # ambiguous sell => deny
    ):
        verdict = option_gate.evaluate(db_session, payload, now=NOW)
        assert verdict.decision == "deny"
        assert "sell-to-open" in verdict.reason


def test_buy_to_close_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(side="buy", position_effect="close"), now=NOW
    )
    assert verdict.decision == "deny"
    assert "buy-to-close" in verdict.reason


def test_multi_leg_denied(db_session):
    setup_live_account(db_session)
    payload = option_order()
    payload["legs"] = [
        {"option_type": "call", "strike_price": 200, "expiration_date": "2026-08-07", "side": "buy"},
        {"option_type": "call", "strike_price": 210, "expiration_date": "2026-08-07", "side": "sell"},
    ]
    verdict = option_gate.evaluate(db_session, payload, now=NOW)
    assert verdict.decision == "deny"
    assert "multi-leg" in verdict.reason


def test_bad_option_type_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(option_type="straddle"), now=NOW
    )
    assert verdict.decision == "deny"
    assert "call or put" in verdict.reason


def test_missing_expiration_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(expiration_date=None), now=NOW
    )
    assert verdict.decision == "deny"
    assert "expiration" in verdict.reason


def test_missing_strike_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(db_session, option_order(strike_price=None), now=NOW)
    assert verdict.decision == "deny"
    assert "strike" in verdict.reason


# --- DTE window ------------------------------------------------------------


def test_dte_too_short_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(expiration_date="2026-07-09"), now=NOW  # 3 DTE < 7
    )
    assert verdict.decision == "deny"
    assert "DTE 3 outside window [7, 45]" in verdict.reason


def test_dte_too_long_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(expiration_date="2026-10-16"), now=NOW  # 102 DTE
    )
    assert verdict.decision == "deny"
    assert "outside window" in verdict.reason


def test_dte_window_follows_params(db_session):
    account = setup_live_account(db_session)
    db_session.add(
        ParamHistory(account_id=account.id, param_name="dte_min_days", new_value="30", actor="human")
    )
    db_session.commit()
    verdict = option_gate.evaluate(
        db_session, option_order(expiration_date="2026-07-24"), now=NOW  # 18 DTE < 30
    )
    assert verdict.decision == "deny"
    assert "[30, 45]" in verdict.reason


def test_sell_to_close_ignores_dte(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    occ_near = "NVDA260708C00200000"
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=1, price=4.0,
        instrument="option", position_key=occ_near,
    )
    fresh_option_quote(db_session, account, occ_near)
    verdict = option_gate.evaluate(
        db_session,
        option_order(
            ref_id="opt-close", side="sell", position_effect="close",
            expiration_date="2026-07-08",  # 2 DTE — closing is still allowed
        ),
        now=NOW,
    )
    assert verdict.decision == "allow", verdict.reason


# --- liquidity -------------------------------------------------------------


def test_low_open_interest_denied(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_option_quote(db_session, account, OCC, open_interest=50)
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "open interest" in verdict.reason


def test_wide_spread_denied(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_option_quote(db_session, account, OCC, bid=4.0, ask=6.0)  # 40% of mid
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "spread" in verdict.reason


def test_stale_option_quote_denied(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_option_quote(db_session, account, OCC, quoted_at=NOW - timedelta(minutes=15))
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "stale" in verdict.reason


def test_no_option_quote_denied(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "no quote" in verdict.reason


# --- sizing / budget -------------------------------------------------------


def test_premium_over_position_cap_denied(db_session):
    setup_live_account(db_session)  # cap 5% of $100k = $5k
    verdict = option_gate.evaluate(
        db_session, option_order(quantity=11, limit_price=5.0), now=NOW  # $5.5k premium
    )
    assert verdict.decision == "deny"
    assert "per-position cap" in verdict.reason


def test_options_sleeve_budget_denied(db_session):
    account = setup_live_account(db_session)  # options sleeve 25% = $25k
    sleeve = ledger.get_sleeve(db_session, account, "options")
    # Raise the concurrent-position limit so budget is the check that fires.
    db_session.add(
        ParamHistory(
            account_id=account.id, param_name="max_concurrent_positions",
            new_value="8", actor="human",
        )
    )
    db_session.commit()
    # $24.9k of premium already deployed across contracts (under 5% cap each)
    for i in range(6):
        add_filled_position(
            db_session, account, sleeve, symbol="NVDA", qty=10, price=4.15,
            instrument="option", position_key=f"NVDA260807C0019{i}000",
            ref_id=f"seed-opt-{i}",
        )
    verdict = option_gate.evaluate(
        db_session, option_order(quantity=2, limit_price=5.0), now=NOW  # $1k > $100 left
    )
    assert verdict.decision == "deny"
    assert "sleeve budget" in verdict.reason


def test_sell_more_contracts_than_held_denied(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    add_filled_position(
        db_session, account, sleeve, symbol="NVDA", qty=1, price=4.0,
        instrument="option", position_key=OCC,
    )
    verdict = option_gate.evaluate(
        db_session,
        option_order(side="sell", position_effect="close", quantity=3),
        now=NOW,
    )
    assert verdict.decision == "deny"
    assert "exceeds held" in verdict.reason


def test_sell_to_close_without_position_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(
        db_session, option_order(side="sell", position_effect="close"), now=NOW
    )
    assert verdict.decision == "deny"
    assert "no open long position" in verdict.reason


# --- dry run + kill switch -------------------------------------------------


def test_dry_run_denies_and_records_simulated(db_session):
    account = make_account(db_session)
    fresh_option_quote(db_session, account, OCC)
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "dry_run" in verdict.reason
    assert get_order(db_session, "opt-0001").status == "simulated"


def test_kill_switch_denies_options_too(db_session):
    from trader.gates import kill_switch

    setup_live_account(db_session)
    kill_switch.update(db_session, 65_000)
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "kill-switch" in verdict.reason


def test_halted_options_sleeve_denied(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "options")
    sleeve.halted = True
    db_session.commit()
    verdict = option_gate.evaluate(db_session, option_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "halted" in verdict.reason


def test_missing_ref_id_denied(db_session):
    setup_live_account(db_session)
    verdict = option_gate.evaluate(db_session, option_order(ref_id=None), now=NOW)
    assert verdict.decision == "deny"
    assert "ref_id" in verdict.reason
