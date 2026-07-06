"""Option order gate — Claude Code PreToolUse hook.

Part of the trust boundary (``trader/gates/`` — human-only).

Wired (in .claude/settings.json, M3) against the Robinhood MCP
``place_option_order`` tool via:

    uv run python -m trader.gates.option_gate

Long single-leg calls/puts only: buy-to-open and sell-to-close. Anything
resembling selling premium or multi-leg structures is denied outright.
Fail-closed everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trader import params as params_mod
from trader.gates import common, market_calendar, runtime
from trader.gates.common import CheckFailed, OrderRequest, Verdict


def _check_structure(order: OrderRequest) -> None:
    if order.leg_count != 1:
        raise CheckFailed(
            "single_leg", f"multi-leg orders are not allowed ({order.leg_count} legs)"
        )
    if order.side not in {"buy", "sell"}:
        raise CheckFailed("order_shape", f"unrecognized side {order.side!r}")
    if order.side == "sell" and order.position_effect != "close":
        raise CheckFailed(
            "long_only",
            "sell-to-open (writing/naked options) is never allowed; sells must "
            "be explicitly position_effect=close",
        )
    if order.side == "buy" and order.position_effect == "close":
        raise CheckFailed(
            "long_only",
            "buy-to-close implies a short option position, which is never allowed",
        )
    if order.option_type not in {"call", "put"}:
        raise CheckFailed("order_shape", f"option_type must be call or put, got {order.option_type!r}")
    if order.qty is None or order.qty <= 0:
        raise CheckFailed("order_shape", f"invalid contract quantity {order.qty}")
    if not order.symbol or not common.EQUITY_SYMBOL_RE.match(order.symbol):
        raise CheckFailed(
            "symbol_format",
            f"underlying {order.symbol!r} is not a listed US equity/ETF ticker format",
        )
    if order.strike is None or order.strike <= 0:
        raise CheckFailed("order_shape", f"invalid strike {order.strike}")
    if order.expiration is None:
        raise CheckFailed("order_shape", "missing/invalid expiration_date (YYYY-MM-DD)")


def _check_dte(order: OrderRequest, current: dict, now: datetime) -> None:
    if order.side != "buy":
        return  # closing an existing long is allowed regardless of remaining DTE
    today = now.astimezone(market_calendar.EASTERN).date()
    dte = (order.expiration - today).days
    lo, hi = int(current["dte_min_days"]), int(current["dte_max_days"])
    if not lo <= dte <= hi:
        raise CheckFailed(
            "dte_window",
            f"DTE {dte} outside window [{lo}, {hi}] (expiration {order.expiration})",
        )


def _check_liquidity(order: OrderRequest, quote) -> None:
    oi = quote.open_interest
    if oi is None or oi < runtime.MIN_OPEN_INTEREST:
        raise CheckFailed(
            "liquidity",
            f"open interest {oi if oi is not None else 'unknown'} below floor "
            f"{runtime.MIN_OPEN_INTEREST} for {order.occ_symbol}",
        )
    if quote.bid is None or quote.ask is None or quote.bid <= 0 or quote.ask <= 0:
        raise CheckFailed("liquidity", f"no usable bid/ask for {order.occ_symbol}")
    mid = (quote.bid + quote.ask) / 2
    spread = (quote.ask - quote.bid) / mid
    if spread > Decimal(str(runtime.MAX_RELATIVE_SPREAD)):
        raise CheckFailed(
            "liquidity",
            f"bid-ask spread {float(spread):.1%} exceeds max "
            f"{runtime.MAX_RELATIVE_SPREAD:.0%} for {order.occ_symbol}",
        )


def evaluate(session, tool_input: dict, now: datetime | None = None) -> Verdict:
    now = now or datetime.now(timezone.utc)
    order = common.parse_order(tool_input, "option")
    config = runtime.load_config()
    checks: list[dict] = []
    account = None
    sleeve = None
    notional = None

    def passed(name: str) -> None:
        checks.append({"name": name, "ok": True})

    try:
        common.check_kill_switch(session, config)
        passed("kill_switch")

        common.check_ref_id(order)
        passed("ref_id")

        account = runtime.get_account(session, config)
        sleeve = common.check_sleeve(session, account, "options")
        passed("sleeve")

        common.check_market_open(now)
        passed("market_open")

        _check_structure(order)
        passed("structure")

        current = params_mod.current(session)
        _check_dte(order, current, now)
        passed("dte_window")

        common.check_trades_per_day(session, account, current, now)
        passed("trades_per_day")

        quote = common.latest_quote(
            session, account, symbol=order.symbol, kind="option", occ=order.occ_symbol
        )
        quote = common.check_quote_fresh(quote, order.occ_symbol, now)
        passed("quote_fresh")

        notional = common.order_notional(order, quote)

        if order.side == "sell":
            common.check_close_has_position(session, sleeve, order)
            passed("long_only")
        else:
            _check_liquidity(order, quote)
            passed("liquidity")
            common.check_concurrent_positions(session, account, order, current)
            passed("concurrent_positions")
            common.check_position_cap(session, sleeve, order, notional, account, current)
            passed("position_cap")
            common.check_sleeve_budget(session, sleeve, order, notional, account)
            passed("sleeve_budget")
    except CheckFailed as exc:
        checks.append({"name": exc.name, "ok": False, "reason": exc.reason})
        verdict = Verdict("deny", f"option gate: {exc.reason}", checks)
        return common.finalize(session, account, sleeve, order, verdict, notional, "denied")

    if runtime.dry_run_enabled(session):
        checks.append({"name": "dry_run", "ok": False, "reason": "dry_run"})
        verdict = Verdict(
            "deny",
            "dry_run: live placement disabled; order passed all checks and was "
            "recorded as simulated",
            checks,
        )
        return common.finalize(session, account, sleeve, order, verdict, notional, "simulated")

    verdict = Verdict(
        "allow",
        f"option gate: approved {order.side} {order.qty}x {order.occ_symbol} "
        f"(~${float(notional):,.2f} premium) ref_id={order.ref_id}",
        checks,
    )
    return common.finalize(session, account, sleeve, order, verdict, notional, "pending")


def main() -> int:
    return common.run_gate_main(evaluate)


if __name__ == "__main__":
    raise SystemExit(main())
