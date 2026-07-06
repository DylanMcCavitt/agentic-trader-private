"""Shared builders for the gate/kill-switch/ledger/reconcile tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trader.db.models import Fill, Order, Quote, utcnow
from trader.gates import kill_switch
from trader.sleeves import ledger

# A regular Monday, 11:00 ET (15:00 UTC during EDT), markets open.
NOW = datetime(2026, 7, 6, 15, 0, tzinfo=timezone.utc)


def make_account(session, equity: float | None = 100_000.0):
    """Account + both sleeves; optionally feed equity through kill_switch."""
    account = ledger.init_sleeves(session)
    if equity is not None:
        kill_switch.update(session, equity)
        session.refresh(account)
    return account


def fresh_equity_quote(
    session,
    account,
    symbol: str = "NVDA",
    *,
    price: float = 100.0,
    avg_dollar_volume: float = 1e9,
    quoted_at: datetime | None = None,
) -> Quote:
    quote = Quote(
        account_id=account.id,
        symbol=symbol,
        kind="equity",
        price=Decimal(str(price)),
        avg_dollar_volume=Decimal(str(avg_dollar_volume)),
        quoted_at=quoted_at or (NOW - timedelta(minutes=1)),
    )
    session.add(quote)
    session.commit()
    return quote


def fresh_option_quote(
    session,
    account,
    occ_symbol: str,
    symbol: str = "NVDA",
    *,
    bid: float = 4.90,
    ask: float = 5.10,
    open_interest: int = 1_000,
    quoted_at: datetime | None = None,
) -> Quote:
    quote = Quote(
        account_id=account.id,
        symbol=symbol,
        kind="option",
        bid=Decimal(str(bid)),
        ask=Decimal(str(ask)),
        open_interest=Decimal(open_interest),
        occ_symbol=occ_symbol,
        quoted_at=quoted_at or (NOW - timedelta(minutes=1)),
    )
    session.add(quote)
    session.commit()
    return quote


def equity_order(**overrides) -> dict:
    order = {
        "ref_id": "eq-0001",
        "symbol": "NVDA",
        "side": "buy",
        "quantity": 10,
        "limit_price": 100.0,
    }
    order.update(overrides)
    return {k: v for k, v in order.items() if v is not None}


def option_order(**overrides) -> dict:
    order = {
        "ref_id": "opt-0001",
        "symbol": "NVDA",
        "side": "buy",
        "position_effect": "open",
        "quantity": 1,
        "limit_price": 5.0,
        "option_type": "call",
        "strike_price": 200.0,
        "expiration_date": "2026-08-07",  # 32 DTE from NOW
    }
    order.update(overrides)
    return {k: v for k, v in order.items() if v is not None}


def add_filled_position(
    session,
    account,
    sleeve,
    *,
    symbol: str = "NVDA",
    qty: float = 10,
    price: float = 100.0,
    instrument: str = "equity",
    ref_id: str | None = None,
    position_key: str | None = None,
    created_at: datetime | None = None,
):
    """A filled buy order + fill so the ledger shows an open position.

    Backdated by default so seeded orders don't consume today's trades/day
    counter; pass ``created_at`` to control that explicitly.
    """
    created_at = created_at or (NOW - timedelta(days=3))
    order = Order(
        account_id=account.id,
        sleeve_id=sleeve.id,
        ref_id=ref_id or f"seed-{symbol}-{qty}-{price}",
        symbol=symbol,
        instrument=instrument,
        side="buy",
        qty=Decimal(str(qty)),
        notional=Decimal(str(qty * price * (100 if instrument == "option" else 1))),
        status="filled",
        payload={"position_key": position_key or symbol},
        created_at=created_at,
    )
    session.add(order)
    session.flush()
    session.add(
        Fill(
            account_id=account.id,
            order_id=order.id,
            qty=Decimal(str(qty)),
            price=Decimal(str(price)),
            filled_at=created_at,
        )
    )
    session.commit()
    return order


def add_pending_order(
    session,
    account,
    sleeve,
    *,
    ref_id: str,
    symbol: str = "AMD",
    notional: float = 1_000.0,
    side: str = "buy",
    position_key: str | None = None,
    created_at: datetime | None = None,
):
    order = Order(
        account_id=account.id,
        sleeve_id=sleeve.id,
        ref_id=ref_id,
        symbol=symbol,
        instrument="equity",
        side=side,
        qty=Decimal("1"),
        notional=Decimal(str(notional)),
        status="pending",
        payload={"position_key": position_key or symbol},
        created_at=created_at or (NOW - timedelta(days=3)),
    )
    session.add(order)
    session.commit()
    return order


def disable_dry_run(session, account):
    from trader.db.models import ParamHistory

    session.add(
        ParamHistory(
            account_id=account.id,
            param_name="dry_run",
            old_value="1",
            new_value="0",
            actor="human",
        )
    )
    session.commit()
