"""Sleeve ledger: budgets, capital attribution, exposure, P&L.

Positions and realized P&L are derived from canonical fills (written by
reconciliation), attributed to the sleeve stamped on each order by the
gates. Pending (gate-approved, not yet reconciled) buy orders count toward
exposure so the gates cannot over-commit a sleeve between reconciliations.

Average-cost accounting: sells realize P&L against the volume-weighted
average cost of the open position. Option contracts carry a 100x
multiplier. Positions are keyed by ``payload["position_key"]`` when the
gate provided one (distinct option contracts on the same underlying stay
separate), else by symbol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select

from trader import params as params_mod
from trader.db.models import Account, Fill, Order, Sleeve
from trader.gates import runtime

OPEN_ORDER_STATUSES = ("pending", "partially_filled")
FILLED_STATUSES = ("filled", "partially_filled")


@dataclass
class Position:
    key: str
    symbol: str
    instrument: str  # equity | option
    qty: Decimal = Decimal(0)
    cost_basis: Decimal = Decimal(0)  # dollars committed to the open qty
    realized_pnl: Decimal = Decimal(0)

    @property
    def multiplier(self) -> int:
        return 100 if self.instrument == "option" else 1

    @property
    def avg_price(self) -> Decimal:
        if self.qty == 0:
            return Decimal(0)
        return self.cost_basis / (self.qty * self.multiplier)


@dataclass
class SleeveReport:
    sleeve_id: int
    type: str
    budget_fraction: float
    budget_dollars: float | None  # None when account equity unknown
    open_exposure: float  # cost basis of open positions
    pending_exposure: float  # gate-approved buys not yet reconciled
    remaining_budget: float | None
    realized_pnl: float
    halted: bool
    positions: list[Position] = field(default_factory=list)


def init_sleeves(session, config: dict | None = None) -> Account:
    """Create the account row and the two sleeves (idempotent)."""
    name = runtime.account_name(config)
    account = session.execute(
        select(Account).where(Account.name == name)
    ).scalar_one_or_none()
    if account is None:
        account = Account(name=name)
        session.add(account)
        session.flush()

    current = params_mod.current(session)
    options_fraction = Decimal(str(current["options_sleeve_budget_fraction"]))
    halt_fraction = Decimal(str(current["sleeve_drawdown_halt_fraction"]))
    existing = {s.type for s in account.sleeves}
    for sleeve_type, fraction in (
        ("equity", Decimal(1) - options_fraction),
        ("options", options_fraction),
    ):
        if sleeve_type not in existing:
            session.add(
                Sleeve(
                    account_id=account.id,
                    type=sleeve_type,
                    budget_fraction=fraction,
                    drawdown_halt_fraction=halt_fraction,
                )
            )
    session.commit()
    session.refresh(account)
    return account


def get_sleeve(session, account: Account, sleeve_type: str) -> Sleeve | None:
    return session.execute(
        select(Sleeve).where(Sleeve.account_id == account.id, Sleeve.type == sleeve_type)
    ).scalar_one_or_none()


def _position_key(order: Order) -> str:
    payload = order.payload or {}
    return str(payload.get("position_key") or order.symbol or f"order-{order.id}")


def positions_for_sleeve(session, sleeve: Sleeve) -> dict[str, Position]:
    """Replay fills in time order to build average-cost positions."""
    rows = session.execute(
        select(Fill, Order)
        .join(Order, Fill.order_id == Order.id)
        .where(Order.sleeve_id == sleeve.id)
        .order_by(Fill.filled_at, Fill.id)
    ).all()

    positions: dict[str, Position] = {}
    for fill, order in rows:
        key = _position_key(order)
        pos = positions.setdefault(
            key,
            Position(key=key, symbol=order.symbol or key, instrument=order.instrument or "equity"),
        )
        mult = pos.multiplier
        if order.side == "buy":
            pos.qty += fill.qty
            pos.cost_basis += fill.qty * fill.price * mult
        else:
            avg = pos.avg_price
            sold = min(fill.qty, pos.qty) if pos.qty > 0 else Decimal(0)
            pos.realized_pnl += (fill.price - avg) * sold * mult
            pos.cost_basis -= avg * sold * mult
            pos.qty -= fill.qty
    return positions


def pending_buy_exposure(session, sleeve: Sleeve) -> Decimal:
    rows = session.execute(
        select(Order).where(
            Order.sleeve_id == sleeve.id,
            Order.side == "buy",
            Order.status.in_(OPEN_ORDER_STATUSES),
        )
    ).scalars()
    return sum((o.notional or Decimal(0)) for o in rows) or Decimal(0)


def open_positions(session, sleeve: Sleeve) -> list[Position]:
    return [p for p in positions_for_sleeve(session, sleeve).values() if p.qty > 0]


def open_position_count(session, account: Account) -> int:
    """Concurrent positions across sleeves: open positions plus pending buy
    orders for keys not already open (each will become a position)."""
    count = 0
    for sleeve in account.sleeves:
        open_keys = {p.key for p in open_positions(session, sleeve)}
        count += len(open_keys)
        pending = session.execute(
            select(Order).where(
                Order.sleeve_id == sleeve.id,
                Order.side == "buy",
                Order.status.in_(OPEN_ORDER_STATUSES),
            )
        ).scalars()
        count += len({_position_key(o) for o in pending} - open_keys)
    return count


def sleeve_report(session, account: Account, sleeve: Sleeve) -> SleeveReport:
    all_positions = positions_for_sleeve(session, sleeve)
    open_pos = [p for p in all_positions.values() if p.qty > 0]
    open_exposure = sum((p.cost_basis for p in open_pos), Decimal(0))
    pending = pending_buy_exposure(session, sleeve)
    realized = sum((p.realized_pnl for p in all_positions.values()), Decimal(0))

    budget_fraction = float(sleeve.budget_fraction)
    if account.equity is not None:
        budget = float(account.equity) * budget_fraction
        remaining = budget - float(open_exposure) - float(pending)
    else:
        budget = None
        remaining = None

    return SleeveReport(
        sleeve_id=sleeve.id,
        type=sleeve.type,
        budget_fraction=budget_fraction,
        budget_dollars=budget,
        open_exposure=float(open_exposure),
        pending_exposure=float(pending),
        remaining_budget=remaining,
        realized_pnl=float(realized),
        halted=sleeve.halted,
        positions=open_pos,
    )
