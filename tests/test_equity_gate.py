from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from trader.db.models import Order
from trader.gates import equity_gate, kill_switch
from trader.sleeves import ledger

from tests.gate_helpers import (
    NOW,
    add_filled_position,
    add_pending_order,
    disable_dry_run,
    equity_order,
    fresh_equity_quote,
    make_account,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("TRADER_CONFIG", "/nonexistent/config.local.json")
    # Never hit the network in tests.
    monkeypatch.setattr(equity_gate, "_yfinance_liquidity", lambda symbol: (None, None))


def setup_live_account(session, equity=100_000.0):
    account = make_account(session, equity=equity)
    disable_dry_run(session, account)
    fresh_equity_quote(session, account, "NVDA")
    return account


def get_order(session, ref_id):
    return session.execute(select(Order).where(Order.ref_id == ref_id)).scalar_one_or_none()


# --- allow paths -----------------------------------------------------------


def test_clean_buy_is_allowed_and_recorded(db_session):
    setup_live_account(db_session)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "allow", verdict.reason

    row = get_order(db_session, "eq-0001")
    assert row is not None
    assert row.status == "pending"
    assert row.symbol == "NVDA"
    assert row.notional == Decimal("1000.0")
    assert row.gate_verdict["decision"] == "allow"
    assert row.payload["quantity"] == 10


def test_sell_to_close_allowed_with_position(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=90)

    verdict = equity_gate.evaluate(
        db_session, equity_order(side="sell", quantity=10), now=NOW
    )
    assert verdict.decision == "allow", verdict.reason


# --- dry run ---------------------------------------------------------------


def test_dry_run_default_denies_but_records_simulated(db_session):
    account = make_account(db_session)  # dry_run not disabled -> defaults ON
    fresh_equity_quote(db_session, account, "NVDA")
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "dry_run" in verdict.reason

    row = get_order(db_session, "eq-0001")
    assert row is not None
    assert row.status == "simulated"


# --- deny paths ------------------------------------------------------------


def test_missing_ref_id_denied_with_instructions(db_session):
    setup_live_account(db_session)
    verdict = equity_gate.evaluate(db_session, equity_order(ref_id=None), now=NOW)
    assert verdict.decision == "deny"
    assert "ref_id" in verdict.reason
    assert db_session.execute(select(Order)).scalars().all() == []


def test_kill_switch_tripped_denies_first(db_session):
    setup_live_account(db_session)
    kill_switch.update(db_session, 65_000)  # 35% below HWM 100k
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "kill-switch" in verdict.reason
    assert verdict.checks[0]["name"] == "kill_switch"
    assert verdict.checks[0]["ok"] is False


def test_unknown_account_equity_denies(db_session):
    account = make_account(db_session, equity=None)
    disable_dry_run(db_session, account)
    fresh_equity_quote(db_session, account, "NVDA")
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "kill-switch" in verdict.reason


def test_halted_sleeve_denies(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    sleeve.halted = True
    db_session.commit()
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "halted" in verdict.reason


def test_market_closed_weekend_denies(db_session):
    setup_live_account(db_session)
    saturday = datetime(2026, 7, 4, 15, 0, tzinfo=timezone.utc)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=saturday)
    assert verdict.decision == "deny"
    assert "market closed" in verdict.reason


def test_market_closed_holiday_denies(db_session):
    setup_live_account(db_session)
    thanksgiving = datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=thanksgiving)
    assert verdict.decision == "deny"
    assert "holiday" in verdict.reason


def test_market_closed_after_early_close_denies(db_session):
    setup_live_account(db_session)
    # Day after Thanksgiving 2026, 13:30 ET (18:30 UTC, EST)
    after_early_close = datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=after_early_close)
    assert verdict.decision == "deny"
    assert "market closed" in verdict.reason


def test_bad_symbol_denied(db_session):
    account = setup_live_account(db_session)
    for bad in ("TOOLONGG", "nvda!", "123", ""):
        verdict = equity_gate.evaluate(
            db_session,
            equity_order(ref_id=f"eq-bad-{bad or 'empty'}", symbol=bad or None),
            now=NOW,
        )
        assert verdict.decision == "deny", bad
        assert "ticker format" in verdict.reason


def test_class_share_symbol_allowed(db_session):
    account = setup_live_account(db_session)
    fresh_equity_quote(db_session, account, "BRK.B", price=400, avg_dollar_volume=5e8)
    verdict = equity_gate.evaluate(
        db_session, equity_order(ref_id="eq-brk", symbol="BRK.B", limit_price=400, quantity=1), now=NOW
    )
    assert verdict.decision == "allow", verdict.reason


def test_sell_to_open_denied(db_session):
    setup_live_account(db_session)
    verdict = equity_gate.evaluate(
        db_session, equity_order(side="sell_to_open"), now=NOW
    )
    assert verdict.decision == "deny"
    assert "short selling" in verdict.reason


def test_trades_per_day_exhausted_denies(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    for i in range(3):  # default max_trades_per_day = 3
        add_pending_order(
            db_session, account, sleeve, ref_id=f"seed-{i}",
            symbol=f"AA{chr(65 + i)}", notional=100,
            created_at=NOW - timedelta(minutes=30),  # placed earlier today
        )
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "trades/day" in verdict.reason


def test_stale_quote_denies(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_equity_quote(
        db_session, account, "NVDA", quoted_at=NOW - timedelta(minutes=11)
    )
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "stale" in verdict.reason


def test_no_quote_denies(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "no quote" in verdict.reason


def test_price_below_floor_denies(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_equity_quote(db_session, account, "NVDA", price=4.50)
    verdict = equity_gate.evaluate(db_session, equity_order(limit_price=4.5), now=NOW)
    assert verdict.decision == "deny"
    assert "below floor $5.00" in verdict.reason


def test_low_dollar_volume_denies(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    fresh_equity_quote(db_session, account, "NVDA", avg_dollar_volume=10_000_000)
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "dollar volume" in verdict.reason


def test_no_liquidity_data_fails_closed(db_session):
    account = make_account(db_session)
    disable_dry_run(db_session, account)
    quote = fresh_equity_quote(db_session, account, "NVDA")
    quote.avg_dollar_volume = None
    db_session.commit()
    # yfinance fallback is monkeypatched to fail in this suite
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "no liquidity data" in verdict.reason


def test_sell_without_position_denied(db_session):
    setup_live_account(db_session)
    verdict = equity_gate.evaluate(db_session, equity_order(side="sell"), now=NOW)
    assert verdict.decision == "deny"
    assert "no open long position" in verdict.reason


def test_sell_more_than_held_denied(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=5, price=90)
    verdict = equity_gate.evaluate(
        db_session, equity_order(side="sell", quantity=10), now=NOW
    )
    assert verdict.decision == "deny"
    assert "exceeds held" in verdict.reason


def test_per_position_cap_denies(db_session):
    setup_live_account(db_session)  # equity 100k, cap 5% = $5k
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=60, limit_price=100.0), now=NOW  # $6k
    )
    assert verdict.decision == "deny"
    assert "per-position cap" in verdict.reason


def test_per_position_cap_includes_existing_position(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=40, price=100)  # $4k held
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=20, limit_price=100.0), now=NOW  # +$2k -> $6k > $5k
    )
    assert verdict.decision == "deny"
    assert "per-position cap" in verdict.reason


def test_sleeve_budget_exceeded_denies(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    # Shrink the sleeve so budget pressure fires before position-count/cap:
    # budget 5% of $100k = $5k, with $4.9k already deployed.
    sleeve.budget_fraction = Decimal("0.05")
    db_session.commit()
    add_filled_position(db_session, account, sleeve, symbol="AMD", qty=49, price=100)
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=20, limit_price=100.0), now=NOW  # $2k > $100 left
    )
    assert verdict.decision == "deny"
    assert "sleeve budget" in verdict.reason


def test_pending_orders_consume_sleeve_budget(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    sleeve.budget_fraction = Decimal("0.05")
    db_session.commit()
    add_pending_order(db_session, account, sleeve, ref_id="p1", symbol="AMD", notional=4_900)
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=20, limit_price=100.0), now=NOW
    )
    assert verdict.decision == "deny"
    assert "sleeve budget" in verdict.reason


def test_concurrent_position_limit_denies(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    for i in range(5):  # default max_concurrent_positions = 5
        add_pending_order(
            db_session, account, sleeve, ref_id=f"seed-{i}", symbol=f"AA{chr(65 + i)}", notional=100
        )
    # Bump trades/day so the position check is what fires.
    from trader.db.models import ParamHistory

    db_session.add(
        ParamHistory(
            account_id=account.id, param_name="max_trades_per_day",
            new_value="6", actor="human",
        )
    )
    db_session.commit()
    verdict = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert verdict.decision == "deny"
    assert "concurrent position" in verdict.reason


def test_adding_to_existing_position_does_not_count_as_new(db_session):
    account = setup_live_account(db_session)
    sleeve = ledger.get_sleeve(db_session, account, "equity")
    for i in range(4):
        add_filled_position(
            db_session, account, sleeve, symbol=f"AA{chr(65 + i)}", qty=1, price=100
        )
    add_filled_position(db_session, account, sleeve, symbol="NVDA", qty=10, price=100)
    # 5 open positions, but NVDA is one of them: adding to it is fine.
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=10, limit_price=100.0), now=NOW
    )
    assert verdict.decision == "allow", verdict.reason


def test_duplicate_ref_id_denied(db_session):
    setup_live_account(db_session)
    first = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert first.decision == "allow"
    second = equity_gate.evaluate(db_session, equity_order(quantity=1), now=NOW)
    assert second.decision == "deny"
    assert "duplicate ref_id" in second.reason


def test_denied_ref_id_can_be_retried(db_session):
    account = setup_live_account(db_session)
    bad = equity_gate.evaluate(
        db_session, equity_order(quantity=60, limit_price=100.0), now=NOW
    )
    assert bad.decision == "deny"
    assert get_order(db_session, "eq-0001").status == "denied"

    good = equity_gate.evaluate(db_session, equity_order(), now=NOW)
    assert good.decision == "allow", good.reason
    assert get_order(db_session, "eq-0001").status == "pending"


def test_deny_verdict_is_recorded(db_session):
    setup_live_account(db_session)
    verdict = equity_gate.evaluate(
        db_session, equity_order(quantity=60, limit_price=100.0), now=NOW
    )
    assert verdict.decision == "deny"
    row = get_order(db_session, "eq-0001")
    assert row.status == "denied"
    assert row.gate_verdict["decision"] == "deny"
    assert any(c["ok"] is False for c in row.gate_verdict["checks"])
