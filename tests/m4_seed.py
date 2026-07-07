"""Shared seeding helpers for M4 digest/journal tests."""

from datetime import datetime, timezone
from decimal import Decimal

from trader.db.models import (
    Account,
    Fill,
    Grade,
    LaneRun,
    Order,
    ParamHistory,
    Quote,
    Sleeve,
    Thesis,
)

DAY = datetime(2026, 7, 6, 14, 30, tzinfo=timezone.utc)


def seed_trading_day(session, *, halted_options: bool = False) -> dict:
    """One full trading day: account, sleeves, thesis, orders, fills, grade,
    a gate rejection, a param change, lane runs, and a quote."""
    account = Account(name="test")
    session.add(account)
    session.flush()

    equity = Sleeve(
        account_id=account.id,
        type="equity",
        budget_fraction=Decimal("0.75"),
        drawdown_halt_fraction=Decimal("0.15"),
        hwm=Decimal("10000.00"),
    )
    options = Sleeve(
        account_id=account.id,
        type="options",
        budget_fraction=Decimal("0.25"),
        drawdown_halt_fraction=Decimal("0.15"),
        halted=halted_options,
        hwm=Decimal("2500.00"),
    )
    session.add_all([equity, options])
    session.flush()

    thesis = Thesis(
        account_id=account.id,
        sleeve_id=equity.id,
        symbol="TQQQ",
        direction="long",
        instrument="etf",
        entry="buy on breakout above 95",
        exit="sell at 105 or +10%",
        invalidation="close below 90",
        status="approved",
        created_at=DAY,
    )
    session.add(thesis)
    session.flush()

    buy = Order(
        account_id=account.id,
        thesis_id=thesis.id,
        ref_id="ref-buy-1",
        side="buy",
        qty=Decimal("10"),
        status="filled",
        created_at=DAY,
    )
    rejected = Order(
        account_id=account.id,
        thesis_id=thesis.id,
        ref_id="ref-rej-1",
        side="buy",
        qty=Decimal("100"),
        status="rejected",
        gate_verdict={"reason": "per-position cap exceeded"},
        created_at=DAY,
    )
    session.add_all([buy, rejected])
    session.flush()

    fill = Fill(
        account_id=account.id,
        order_id=buy.id,
        qty=Decimal("10"),
        price=Decimal("96.00"),
        filled_at=DAY,
    )
    grade = Grade(
        account_id=account.id,
        thesis_id=thesis.id,
        score=Decimal("7.5"),
        notes="good entry, exit plan held",
        created_at=DAY,
    )
    param_change = ParamHistory(
        account_id=account.id,
        param_name="max_trades_per_day",
        old_value="3",
        new_value="4",
        evidence="win rate supports more attempts",
        actor="improve-lane",
        created_at=DAY,
    )
    lane_run = LaneRun(
        account_id=account.id,
        lane="research",
        started_at=DAY,
        finished_at=DAY,
        status="succeeded",
        summary="3 candidates",
    )
    quote = Quote(account_id=account.id, symbol="TQQQ", quoted_at=DAY, price=Decimal("99.00"))
    session.add_all([fill, grade, param_change, lane_run, quote])
    session.commit()

    return {
        "account": account,
        "equity": equity,
        "options": options,
        "thesis": thesis,
        "buy": buy,
        "rejected": rejected,
    }
