"""Reconciliation: match broker orders against gate-approved orders.

The execution/review lane fetches the broker's order list from the
Robinhood MCP (get_equity_orders / get_option_orders) and passes it here as
JSON (file or stdin). We:

- match each broker order to ``orders.ref_id`` (client order id),
- write canonical :class:`Fill` rows for executions,
- update order status (filled / partially_filled / cancelled / rejected),
- FLAG loudly (nonzero exit + ``lane_runs`` event row) any broker order with
  no matching ref_id (an order the gates never approved — unauthorized) and
  any gate-approved order the broker has no record of (a lost order).

Broker order JSON is parsed tolerantly. Expected shape per order:
  {"ref_id"/"client_order_id": ..., "state"/"status": ...,
   "executions"/"fills": [{"quantity": ..., "price": ...,
                           "timestamp"/"settlement_date": ...}], ...}
A flat list or {"orders"/"results": [...]} envelope both work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from trader.db.models import Account, Fill, LaneRun, Order, utcnow
from trader.gates import runtime

# Broker states mapped onto our order statuses.
TERMINAL_STATES = {
    "filled": "filled",
    "executed": "filled",
    "partially_filled": "partially_filled",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "rejected": "rejected",
    "failed": "rejected",
    "expired": "cancelled",
}
# Gate statuses that expect a broker record.
EXPECTS_BROKER_RECORD = {"pending", "partially_filled"}


@dataclass
class ReconcileResult:
    matched: int = 0
    fills_written: int = 0
    unauthorized: list[dict[str, Any]] = field(default_factory=list)
    missing_at_broker: list[str] = field(default_factory=list)  # ref_ids

    @property
    def clean(self) -> bool:
        return not self.unauthorized and not self.missing_at_broker


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_when(value: Any) -> datetime:
    if value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return utcnow()


def parse_broker_orders(raw: Any) -> list[dict[str, Any]]:
    """Accept a list, or an envelope dict with orders/results/data keys."""
    if isinstance(raw, str):
        raw = json.loads(raw)
    if isinstance(raw, dict):
        for key in ("orders", "results", "data"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            raise ValueError("broker payload is a dict without an orders/results list")
    if not isinstance(raw, list):
        raise ValueError("broker payload must be a JSON list of orders")
    return [o for o in raw if isinstance(o, dict)]


def reconcile(session, broker_orders: list[dict[str, Any]], config: dict | None = None) -> ReconcileResult:
    account = runtime.get_account(session, config)
    if account is None:
        raise ValueError("no account row — run `trader sleeves init` first")

    result = ReconcileResult()
    seen_ref_ids: set[str] = set()

    for broker in broker_orders:
        ref_id = _first(broker, "ref_id", "client_order_id", "clientOrderId", "client_ref_id")
        ref_id = str(ref_id).strip() if ref_id is not None and str(ref_id).strip() else None
        order = None
        if ref_id:
            order = session.execute(
                select(Order).where(Order.ref_id == ref_id, Order.account_id == account.id)
            ).scalar_one_or_none()
        if order is None:
            result.unauthorized.append(broker)
            continue

        seen_ref_ids.add(ref_id)
        result.matched += 1

        executions = _first(broker, "executions", "fills") or []
        # Query directly (not order.fills) so a session-cached relationship
        # can never hide fills written by an earlier reconcile run.
        existing_fill_qty = sum(
            (
                f.qty
                for f in session.execute(
                    select(Fill).where(Fill.order_id == order.id)
                ).scalars()
            ),
            Decimal(0),
        )
        new_qty = Decimal(0)
        for ex in executions:
            if not isinstance(ex, dict):
                continue
            qty = _to_decimal(_first(ex, "quantity", "qty"))
            price = _to_decimal(_first(ex, "price", "execution_price"))
            if qty is None or price is None:
                continue
            new_qty += qty
        # Idempotency: only append the delta beyond what we already recorded.
        delta = new_qty - existing_fill_qty
        if delta > 0:
            # Volume-weighted price of the broker-reported executions.
            total_qty = Decimal(0)
            total_cost = Decimal(0)
            when = utcnow()
            for ex in executions:
                if not isinstance(ex, dict):
                    continue
                qty = _to_decimal(_first(ex, "quantity", "qty"))
                price = _to_decimal(_first(ex, "price", "execution_price"))
                if qty is None or price is None:
                    continue
                total_qty += qty
                total_cost += qty * price
                when = _parse_when(_first(ex, "timestamp", "executed_at", "settlement_date"))
            vwap = total_cost / total_qty if total_qty > 0 else Decimal(0)
            session.add(
                Fill(
                    account_id=account.id,
                    order_id=order.id,
                    qty=delta,
                    price=vwap,
                    filled_at=when,
                    raw=broker,
                )
            )
            result.fills_written += 1

        state = str(_first(broker, "state", "status") or "").strip().lower()
        if state in TERMINAL_STATES:
            order.status = TERMINAL_STATES[state]
        elif delta > 0 and order.status == "pending":
            order.status = "partially_filled"

    # Gate-approved orders the broker has no record of.
    pending = session.execute(
        select(Order).where(
            Order.account_id == account.id,
            Order.status.in_(EXPECTS_BROKER_RECORD),
        )
    ).scalars()
    for order in pending:
        if order.ref_id not in seen_ref_ids:
            result.missing_at_broker.append(order.ref_id)
            order.status = "unmatched"

    event_status = "ok" if result.clean else "flagged"
    session.add(
        LaneRun(
            account_id=account.id,
            lane="reconcile",
            finished_at=utcnow(),
            status=event_status,
            summary=(
                f"matched={result.matched} fills={result.fills_written} "
                f"unauthorized={len(result.unauthorized)} "
                f"missing_at_broker={len(result.missing_at_broker)}"
            ),
            artifact={
                "unauthorized": result.unauthorized,
                "missing_at_broker": result.missing_at_broker,
            },
        )
    )
    session.commit()
    return result
