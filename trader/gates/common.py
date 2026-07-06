"""Shared gate machinery: hook protocol I/O, order parsing, checks, verdicts.

Part of the trust boundary (``trader/gates/`` — human-only).

Claude Code PreToolUse hook protocol (kept tolerant on input):
  stdin:  {"tool_name": ..., "tool_input": {...}, ...}
  stdout: {"hookSpecificOutput": {"hookEventName": "PreToolUse",
           "permissionDecision": "allow"|"deny",
           "permissionDecisionReason": "..."}}

Every path that cannot positively verify an order is safe DENIES
(fail-closed): malformed input, DB errors, unknown account state, missing
quotes, uncovered calendar years, and so on.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import select

from trader import params as params_mod
from trader.db.models import Account, Order, Quote, Sleeve
from trader.gates import kill_switch, market_calendar, runtime
from trader.sleeves import ledger

EQUITY_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}([.-][A-Z]{1,2})?$")

# Live-order statuses that count toward the trades/day cap (everything the
# gate approved for placement, plus simulated dry-run orders so a rehearsal
# exercises the counter; only denied/unmatched rows are excluded).
TRADES_PER_DAY_STATUSES_EXCLUDED = {"denied", "unmatched"}


# --------------------------------------------------------------------------
# Hook protocol I/O


def emit_decision(decision: str, reason: str) -> None:
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": decision,
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.stdout.write("\n")


def run_gate_main(evaluate: Callable, stdin=None) -> int:
    """Entrypoint shared by equity_gate/option_gate __main__ blocks.

    ``evaluate(session, tool_input)`` returns a :class:`Verdict`. Any
    exception anywhere denies (fail closed). Always exits 0 so the decision
    JSON is what Claude Code consumes.
    """
    try:
        payload = json.load(stdin or sys.stdin)
    except Exception:
        emit_decision("deny", "gate: malformed hook input JSON (fail closed)")
        return 0

    tool_input = payload.get("tool_input") if isinstance(payload, dict) else None
    if not isinstance(tool_input, dict):
        emit_decision("deny", "gate: hook input missing tool_input object (fail closed)")
        return 0

    try:
        from trader.db.session import get_session

        session = get_session()
        try:
            verdict = evaluate(session, tool_input)
        finally:
            session.close()
    except Exception as exc:
        emit_decision(
            "deny", f"gate: internal error {exc.__class__.__name__} (fail closed)"
        )
        return 0

    emit_decision(verdict.decision, verdict.reason)
    return 0


# --------------------------------------------------------------------------
# Order parsing (tolerant of key-name variants across MCP server versions)


def _first(d: dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


@dataclass
class OrderRequest:
    ref_id: str | None
    symbol: str | None
    side: str | None  # buy | sell
    position_effect: str | None  # open | close (None => inferred later)
    qty: Decimal | None
    limit_price: Decimal | None
    instrument: str  # equity | option
    expiration: date | None = None
    strike: Decimal | None = None
    option_type: str | None = None  # call | put
    leg_count: int = 1
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def position_key(self) -> str | None:
        if self.instrument == "option":
            return self.occ_symbol
        return self.symbol

    @property
    def occ_symbol(self) -> str | None:
        if (
            self.symbol is None
            or self.expiration is None
            or self.option_type is None
            or self.strike is None
        ):
            return None
        cp = "C" if self.option_type == "call" else "P"
        return f"{self.symbol}{self.expiration:%y%m%d}{cp}{int(self.strike * 1000):08d}"


def _parse_side_effect(raw_side: Any, raw_effect: Any) -> tuple[str | None, str | None]:
    side = str(raw_side).strip().lower() if raw_side is not None else None
    effect = str(raw_effect).strip().lower() if raw_effect is not None else None
    if side in {"buy_to_open", "bto"}:
        return "buy", "open"
    if side in {"sell_to_close", "stc"}:
        return "sell", "close"
    if side in {"sell_to_open", "sto"}:
        return "sell", "open"
    if side in {"buy_to_close", "btc"}:
        return "buy", "close"
    if side not in {"buy", "sell", None}:
        return None, None  # unrecognized side => deny downstream
    if effect in {"open", "opening"}:
        effect = "open"
    elif effect in {"close", "closing"}:
        effect = "close"
    elif effect is not None:
        effect = None
    return side, effect


def parse_order(tool_input: dict[str, Any], instrument: str) -> OrderRequest:
    ref_id = _first(tool_input, "ref_id", "client_order_id", "clientOrderId", "client_ref_id")
    symbol = _first(tool_input, "symbol", "ticker", "underlying_symbol", "underlying")
    side, effect = _parse_side_effect(
        _first(tool_input, "side", "action", "direction"),
        _first(tool_input, "position_effect", "positionEffect", "effect", "open_close"),
    )
    qty = _to_decimal(_first(tool_input, "quantity", "qty", "shares", "contracts"))
    limit_price = _to_decimal(
        _first(tool_input, "limit_price", "limitPrice", "price", "premium")
    )

    order = OrderRequest(
        ref_id=str(ref_id).strip() if ref_id is not None and str(ref_id).strip() else None,
        symbol=str(symbol).strip().upper() if symbol else None,
        side=side,
        position_effect=effect,
        qty=qty,
        limit_price=limit_price,
        instrument=instrument,
        raw=tool_input,
    )

    if instrument == "option":
        legs = tool_input.get("legs")
        if isinstance(legs, list) and legs:
            order.leg_count = len(legs)
            leg = legs[0] if isinstance(legs[0], dict) else {}
        else:
            leg = tool_input
        exp = _first(leg, "expiration_date", "expirationDate", "expiration", "expiry")
        if exp is not None:
            try:
                order.expiration = date.fromisoformat(str(exp)[:10])
            except ValueError:
                order.expiration = None
        order.strike = _to_decimal(_first(leg, "strike_price", "strikePrice", "strike"))
        opt_type = _first(leg, "option_type", "optionType", "type", "contract_type")
        if opt_type is not None:
            opt_type = str(opt_type).strip().lower()
        order.option_type = opt_type if opt_type in {"call", "put"} else None
        # Leg-level side/effect (Robinhood-style multi-leg payloads) override
        # only when the top level did not specify them.
        if order.side is None or order.position_effect is None:
            leg_side, leg_effect = _parse_side_effect(
                _first(leg, "side", "action"),
                _first(leg, "position_effect", "positionEffect", "effect"),
            )
            order.side = order.side or leg_side
            order.position_effect = order.position_effect or leg_effect

    return order


# --------------------------------------------------------------------------
# Verdicts and check running


@dataclass
class Verdict:
    decision: str  # allow | deny
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


class CheckFailed(Exception):
    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason
        super().__init__(reason)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Individual checks (raise CheckFailed to deny)


def check_kill_switch(session, config) -> None:
    halted, reason = kill_switch.account_halted(session, config)
    if halted:
        raise CheckFailed("kill_switch", f"kill-switch: {reason}")


def check_ref_id(order: OrderRequest) -> None:
    if not order.ref_id:
        raise CheckFailed(
            "ref_id",
            "missing ref_id: compose the order with a unique client order id "
            "(ref_id) so reconciliation can match it to the broker record, "
            "then retry",
        )


def check_sleeve(session, account: Account, sleeve_type: str) -> Sleeve:
    sleeve = ledger.get_sleeve(session, account, sleeve_type)
    if sleeve is None:
        raise CheckFailed(
            "sleeve_exists",
            f"{sleeve_type} sleeve not found — run `trader sleeves init`",
        )
    halted, reason = kill_switch.sleeve_halted(session, sleeve)
    if halted:
        raise CheckFailed("sleeve_halted", reason)
    return sleeve


def check_market_open(now: datetime) -> None:
    if not market_calendar.is_market_open(now):
        raise CheckFailed(
            "market_open", f"market closed: {market_calendar.market_status(now)}"
        )


def check_trades_per_day(session, account: Account, current: dict, now: datetime) -> None:
    limit = int(current["max_trades_per_day"])
    et_now = now.astimezone(market_calendar.EASTERN)
    today = et_now.date()
    rows = session.execute(select(Order).where(Order.account_id == account.id)).scalars()
    count = 0
    for row in rows:
        if row.status in TRADES_PER_DAY_STATUSES_EXCLUDED:
            continue
        created = _as_utc(row.created_at)
        if created is not None and created.astimezone(market_calendar.EASTERN).date() == today:
            count += 1
    if count >= limit:
        raise CheckFailed(
            "trades_per_day", f"trades/day limit reached ({count}/{limit} today)"
        )


def check_concurrent_positions(
    session, account: Account, order: OrderRequest, current: dict
) -> None:
    if order.side != "buy" or order.position_effect == "close":
        return
    limit = int(current["max_concurrent_positions"])
    open_keys: set[str] = set()
    for sleeve in account.sleeves:
        open_keys |= {p.key for p in ledger.open_positions(session, sleeve)}
        pending = session.execute(
            select(Order).where(
                Order.sleeve_id == sleeve.id,
                Order.side == "buy",
                Order.status.in_(ledger.OPEN_ORDER_STATUSES),
            )
        ).scalars()
        open_keys |= {ledger._position_key(o) for o in pending}
    prospective = len(open_keys) + (0 if order.position_key in open_keys else 1)
    if prospective > limit:
        raise CheckFailed(
            "concurrent_positions",
            f"concurrent position limit: {len(open_keys)} open/pending, max {limit}",
        )


def latest_quote(session, account: Account, *, symbol: str, kind: str, occ: str | None = None) -> Quote | None:
    stmt = select(Quote).where(Quote.account_id == account.id, Quote.kind == kind)
    if kind == "option" and occ is not None:
        stmt = stmt.where(Quote.occ_symbol == occ)
    else:
        stmt = stmt.where(Quote.symbol == symbol)
    stmt = stmt.order_by(Quote.quoted_at.desc(), Quote.id.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def check_quote_fresh(quote: Quote | None, what: str, now: datetime) -> Quote:
    if quote is None:
        raise CheckFailed("quote_fresh", f"no quote recorded for {what} — store one first")
    quoted_at = _as_utc(quote.quoted_at)
    age = now.astimezone(timezone.utc) - quoted_at
    max_age = timedelta(minutes=runtime.QUOTE_FRESHNESS_MINUTES)
    if age > max_age:
        raise CheckFailed(
            "quote_fresh",
            f"quote for {what} is stale ({age.total_seconds() / 60:.0f} min old, "
            f"max {runtime.QUOTE_FRESHNESS_MINUTES})",
        )
    return quote


def order_notional(order: OrderRequest, quote: Quote | None) -> Decimal:
    """Dollars this order commits. Uses limit price when given, else quote."""
    price = order.limit_price
    if price is None and quote is not None:
        if quote.price is not None:
            price = quote.price
        elif quote.bid is not None and quote.ask is not None:
            price = (quote.bid + quote.ask) / 2
    if price is None or order.qty is None:
        raise CheckFailed(
            "notional", "cannot price order: no limit price and no usable quote"
        )
    mult = 100 if order.instrument == "option" else 1
    return order.qty * price * mult


def check_position_cap(
    session,
    sleeve: Sleeve,
    order: OrderRequest,
    notional: Decimal,
    account: Account,
    current: dict,
) -> None:
    if order.side != "buy":
        return
    cap = Decimal(str(current["per_position_max_fraction"])) * Decimal(account.equity)
    existing = Decimal(0)
    positions = ledger.positions_for_sleeve(session, sleeve)
    pos = positions.get(order.position_key or "")
    if pos is not None and pos.qty > 0:
        existing += pos.cost_basis
    pending = session.execute(
        select(Order).where(
            Order.sleeve_id == sleeve.id,
            Order.side == "buy",
            Order.status.in_(ledger.OPEN_ORDER_STATUSES),
        )
    ).scalars()
    for p in pending:
        if ledger._position_key(p) == order.position_key:
            existing += p.notional or Decimal(0)
    if existing + notional > cap:
        raise CheckFailed(
            "position_cap",
            f"per-position cap exceeded: ${float(existing + notional):,.2f} "
            f"would exceed cap ${float(cap):,.2f} "
            f"({float(current['per_position_max_fraction']):.1%} of equity)",
        )


def check_sleeve_budget(
    session, sleeve: Sleeve, order: OrderRequest, notional: Decimal, account: Account
) -> None:
    if order.side != "buy":
        return
    report = ledger.sleeve_report(session, account, sleeve)
    if report.remaining_budget is None:
        raise CheckFailed("sleeve_budget", "account equity unknown — cannot size sleeve budget")
    if float(notional) > report.remaining_budget:
        raise CheckFailed(
            "sleeve_budget",
            f"{sleeve.type} sleeve budget exceeded: order ${float(notional):,.2f} > "
            f"remaining ${report.remaining_budget:,.2f} "
            f"(budget ${report.budget_dollars:,.2f}, exposure "
            f"${report.open_exposure + report.pending_exposure:,.2f})",
        )


def check_close_has_position(session, sleeve: Sleeve, order: OrderRequest) -> None:
    """Sells must close an existing long position (long-only, never short)."""
    positions = ledger.positions_for_sleeve(session, sleeve)
    pos = positions.get(order.position_key or "")
    held = pos.qty if pos is not None else Decimal(0)
    if held <= 0:
        raise CheckFailed(
            "long_only",
            f"sell denied: no open long position in {order.position_key} "
            "(short selling / naked positions are never allowed)",
        )
    if order.qty is None or order.qty > held:
        raise CheckFailed(
            "long_only",
            f"sell denied: qty {order.qty} exceeds held {held} in {order.position_key}",
        )


# --------------------------------------------------------------------------
# Recording verdicts in the orders table


def record_order(
    session,
    account: Account,
    sleeve: Sleeve | None,
    order: OrderRequest,
    verdict: Verdict,
    notional: Decimal | None,
    status: str,
) -> None:
    """Persist the gate verdict + full composed payload keyed by ref_id.

    A previously *denied* ref_id may be retried (the row is updated); a
    ref_id that was ever approved/simulated can never be reused.
    """
    if not order.ref_id:
        return
    existing = session.execute(
        select(Order).where(Order.ref_id == order.ref_id)
    ).scalar_one_or_none()
    verdict_json = {
        "decision": verdict.decision,
        "reason": verdict.reason,
        "checks": verdict.checks,
    }
    if existing is not None:
        if existing.status != "denied":
            raise CheckFailed(
                "ref_id_unique",
                f"duplicate ref_id {order.ref_id!r} (already recorded with "
                f"status {existing.status}) — compose a fresh ref_id",
            )
        row = existing
    else:
        row = Order(account_id=account.id, ref_id=order.ref_id, side=order.side or "buy")
        session.add(row)
    row.sleeve_id = sleeve.id if sleeve is not None else None
    row.symbol = order.symbol
    row.instrument = order.instrument
    row.side = order.side or "buy"
    row.qty = order.qty
    row.notional = notional
    row.status = status
    row.gate_verdict = verdict_json
    payload = dict(order.raw)
    if order.position_key:
        payload.setdefault("position_key", order.position_key)
    row.payload = payload
    session.commit()


def finalize(
    session,
    account: Account | None,
    sleeve: Sleeve | None,
    order: OrderRequest,
    verdict: Verdict,
    notional: Decimal | None,
    status: str,
) -> Verdict:
    """Record the verdict row; recording failures convert to a deny."""
    if account is None:
        return verdict
    try:
        record_order(session, account, sleeve, order, verdict, notional, status)
    except CheckFailed as exc:
        return Verdict("deny", exc.reason, verdict.checks)
    except Exception as exc:
        session.rollback()
        return Verdict(
            "deny",
            f"failed to record order ({exc.__class__.__name__}) — fail closed",
            verdict.checks,
        )
    return verdict
