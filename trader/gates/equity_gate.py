"""Equity order gate — Claude Code PreToolUse hook.

Part of the trust boundary (``trader/gates/`` — human-only).

Wired (in .claude/settings.json, M3) against the Robinhood MCP
``place_equity_order`` tool via:

    uv run python -m trader.gates.equity_gate

Reads the PreToolUse hook JSON from stdin, evaluates every risk check, and
prints an allow/deny decision. Fail-closed everywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from trader import params as params_mod
from trader.gates import common, runtime
from trader.gates.common import CheckFailed, OrderRequest, Verdict


def _check_symbol(order: OrderRequest) -> None:
    if not order.symbol or not common.EQUITY_SYMBOL_RE.match(order.symbol):
        raise CheckFailed(
            "symbol_format",
            f"symbol {order.symbol!r} is not a listed US equity/ETF ticker format",
        )


def _check_order_shape(order: OrderRequest) -> None:
    if order.side not in {"buy", "sell"}:
        raise CheckFailed("order_shape", f"unrecognized side {order.side!r}")
    if order.position_effect == "open" and order.side == "sell":
        raise CheckFailed("long_only", "sell-to-open (short selling) is never allowed")
    if order.qty is None or order.qty <= 0:
        raise CheckFailed("order_shape", f"invalid quantity {order.qty}")


def _yfinance_liquidity(symbol: str) -> tuple[Decimal | None, Decimal | None]:
    """(price, avg_dollar_volume) fallback. Any failure -> (None, None)."""
    try:
        import yfinance as yf

        hist = yf.Ticker(symbol).history(period="1mo", auto_adjust=False)
        if hist is None or hist.empty:
            return None, None
        price = Decimal(str(float(hist["Close"].iloc[-1])))
        adv = Decimal(str(float((hist["Close"] * hist["Volume"]).mean())))
        return price, adv
    except Exception:
        return None, None


def _check_liquidity(order: OrderRequest, quote) -> None:
    price = quote.price if quote is not None else None
    adv = quote.avg_dollar_volume if quote is not None else None
    if price is None or adv is None:
        yf_price, yf_adv = _yfinance_liquidity(order.symbol)
        price = price if price is not None else yf_price
        adv = adv if adv is not None else yf_adv
    if price is None or adv is None:
        raise CheckFailed(
            "liquidity",
            f"no liquidity data for {order.symbol} (quote snapshot lacks "
            "price/avg dollar volume and yfinance fallback failed)",
        )
    if price < Decimal(str(runtime.MIN_PRICE)):
        raise CheckFailed(
            "liquidity",
            f"{order.symbol} price ${float(price):.2f} below floor ${runtime.MIN_PRICE:.2f}",
        )
    if adv < Decimal(str(runtime.MIN_AVG_DOLLAR_VOLUME)):
        raise CheckFailed(
            "liquidity",
            f"{order.symbol} avg daily dollar volume ${float(adv):,.0f} below "
            f"floor ${runtime.MIN_AVG_DOLLAR_VOLUME:,.0f}",
        )


def evaluate(session, tool_input: dict, now: datetime | None = None) -> Verdict:
    now = now or datetime.now(timezone.utc)
    order = common.parse_order(tool_input, "equity")
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
        sleeve = common.check_sleeve(session, account, "equity")
        passed("sleeve")

        common.check_market_open(now)
        passed("market_open")

        _check_order_shape(order)
        _check_symbol(order)
        passed("order_shape")

        current = params_mod.current(session)
        common.check_trades_per_day(session, account, current, now)
        passed("trades_per_day")

        quote = common.latest_quote(session, account, symbol=order.symbol, kind="equity")
        quote = common.check_quote_fresh(quote, order.symbol, now)
        passed("quote_fresh")

        notional = common.order_notional(order, quote)

        if order.side == "sell":
            # Liquidity floors apply to opening buys only — never block an exit.
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
        verdict = Verdict("deny", f"equity gate: {exc.reason}", checks)
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
        f"equity gate: approved {order.side} {order.qty} {order.symbol} "
        f"(~${float(notional):,.2f}) ref_id={order.ref_id}",
        checks,
    )
    return common.finalize(session, account, sleeve, order, verdict, notional, "pending")


def main() -> int:
    return common.run_gate_main(evaluate)


if __name__ == "__main__":
    raise SystemExit(main())
