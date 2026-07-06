"""Day-scoped DB queries shared by the digest and the journal writer.

A "day" is a UTC calendar date; every query takes ``[day 00:00, day+1)``
half-open ranges so digest and journal agree on what belongs to a date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from trader.db.models import (
    Fill,
    Grade,
    LaneRun,
    Order,
    ParamHistory,
    Quote,
    Sleeve,
    Thesis,
)


def fmt_dec(value: Decimal | None, places: int | None = None) -> str:
    """Render a Decimal without storage-scale noise (96.0000 -> 96)."""
    if value is None:
        return "n/a"
    if places is not None:
        return f"{value:.{places}f}"
    normalized = value.normalize()
    if normalized == normalized.to_integral_value():
        return str(normalized.quantize(Decimal(1)))
    return format(normalized, "f")


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _in_day(column, day: date):
    start, end = day_bounds(day)
    return (column >= start) & (column < end)


@dataclass
class Position:
    symbol: str
    sleeve_type: str
    qty: Decimal
    cost_basis: Decimal  # weighted average buy price
    thesis: Thesis | None = None
    current_price: Decimal | None = None

    @property
    def unrealized_pnl(self) -> Decimal | None:
        if self.current_price is None:
            return None
        return (self.current_price - self.cost_basis) * self.qty


@dataclass
class DayEvents:
    day: date
    lane_runs: list[LaneRun] = field(default_factory=list)
    theses_created: list[Thesis] = field(default_factory=list)
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    grades: list[Grade] = field(default_factory=list)
    param_changes: list[ParamHistory] = field(default_factory=list)
    gate_rejections: list[Order] = field(default_factory=list)
    halted_sleeves: list[Sleeve] = field(default_factory=list)
    sleeves: list[Sleeve] = field(default_factory=list)


def load_day_events(session, day: date) -> DayEvents:
    ev = DayEvents(day=day)
    ev.lane_runs = list(
        session.execute(
            select(LaneRun).where(_in_day(LaneRun.started_at, day)).order_by(LaneRun.started_at)
        ).scalars()
    )
    ev.theses_created = list(
        session.execute(
            select(Thesis).where(_in_day(Thesis.created_at, day)).order_by(Thesis.created_at)
        ).scalars()
    )
    ev.orders = list(
        session.execute(
            select(Order).where(_in_day(Order.created_at, day)).order_by(Order.created_at)
        ).scalars()
    )
    ev.fills = list(
        session.execute(
            select(Fill).where(_in_day(Fill.filled_at, day)).order_by(Fill.filled_at)
        ).scalars()
    )
    ev.grades = list(
        session.execute(
            select(Grade).where(_in_day(Grade.created_at, day)).order_by(Grade.created_at)
        ).scalars()
    )
    ev.param_changes = list(
        session.execute(
            select(ParamHistory)
            .where(_in_day(ParamHistory.created_at, day))
            .order_by(ParamHistory.created_at)
        ).scalars()
    )
    ev.gate_rejections = [o for o in ev.orders if o.status == "rejected"]
    ev.sleeves = list(session.execute(select(Sleeve).order_by(Sleeve.id)).scalars())
    ev.halted_sleeves = [s for s in ev.sleeves if s.halted]
    return ev


def sleeve_type_for_order(session, order: Order) -> str:
    if order.thesis is not None:
        sleeve = session.get(Sleeve, order.thesis.sleeve_id)
        if sleeve is not None:
            return sleeve.type
    return "unassigned"


def realized_pnl_by_sleeve(session, day: date) -> dict[str, Decimal]:
    """Net cash flow from the day's fills per sleeve: sells - buys.

    This is cash P&L for the day (a buy shows as negative until it exits),
    which is the honest number without a full positions engine.
    """
    start, end = day_bounds(day)
    fills = session.execute(
        select(Fill).where((Fill.filled_at >= start) & (Fill.filled_at < end))
    ).scalars()
    pnl: dict[str, Decimal] = {}
    for fill in fills:
        order = session.get(Order, fill.order_id)
        sleeve_type = sleeve_type_for_order(session, order) if order else "unassigned"
        sign = Decimal(1) if order and order.side == "sell" else Decimal(-1)
        pnl[sleeve_type] = pnl.get(sleeve_type, Decimal(0)) + sign * fill.qty * fill.price
    return pnl


def open_positions(session, as_of: date) -> list[Position]:
    """Net positions from all fills up to end of ``as_of``.

    Symbol comes from the order's thesis. Fills on orders without a thesis
    are grouped under 'unassigned'.
    """
    _, end = day_bounds(as_of)
    fills = list(
        session.execute(select(Fill).where(Fill.filled_at < end).order_by(Fill.filled_at)).scalars()
    )

    agg: dict[tuple[str, str], dict] = {}
    for fill in fills:
        order = session.get(Order, fill.order_id)
        thesis = order.thesis if order else None
        symbol = thesis.symbol if thesis else "?"
        sleeve_type = sleeve_type_for_order(session, order) if order else "unassigned"
        key = (sleeve_type, symbol)
        entry = agg.setdefault(
            key, {"qty": Decimal(0), "buy_qty": Decimal(0), "buy_cost": Decimal(0), "thesis": None}
        )
        if order and order.side == "sell":
            entry["qty"] -= fill.qty
        else:
            entry["qty"] += fill.qty
            entry["buy_qty"] += fill.qty
            entry["buy_cost"] += fill.qty * fill.price
        if thesis is not None:
            entry["thesis"] = thesis

    positions = []
    for (sleeve_type, symbol), entry in sorted(agg.items()):
        if entry["qty"] == 0:
            continue
        basis = entry["buy_cost"] / entry["buy_qty"] if entry["buy_qty"] else Decimal(0)
        positions.append(
            Position(
                symbol=symbol,
                sleeve_type=sleeve_type,
                qty=entry["qty"],
                cost_basis=basis,
                thesis=entry["thesis"],
                current_price=latest_quote_price(session, symbol),
            )
        )
    return positions


def latest_quote_price(session, symbol: str) -> Decimal | None:
    quote = session.execute(
        select(Quote)
        .where(Quote.symbol == symbol, Quote.price.is_not(None))
        .order_by(Quote.quoted_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return quote.price if quote else None
