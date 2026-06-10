#!/usr/bin/env python3
"""PreToolUse hook: deterministic gate on place_equity_order.

Reads the hook payload from stdin. Exit 2 blocks the order (stderr is shown
to the model); exit 0 allows it. This gate, not the model, is the real
guardrail — keep every rule here mechanical.
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent


def block(msg: str) -> None:
    print(f"ORDER BLOCKED: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> None:
    payload = json.load(sys.stdin)
    if not payload.get("tool_name", "").endswith("place_equity_order"):
        sys.exit(0)

    cfg = json.loads((ROOT / "config.json").read_text())
    state = json.loads((ROOT / "state" / "state.json").read_text())
    order = payload.get("tool_input", {})

    if cfg.get("dry_run"):
        block("dry_run=true in config.json — review only, no live orders")
    if state.get("halt"):
        block(f"kill switch active: {state.get('halt_reason')}")
    if order.get("account_number") != cfg["account_number"]:
        block(f"account {order.get('account_number')!r} is not the agentic account")
    if order.get("symbol") != cfg["symbol"]:
        block(f"symbol {order.get('symbol')!r} not whitelisted (only {cfg['symbol']})")

    side = order.get("side")
    if side == "buy":
        if order.get("type") != "market" or "dollar_amount" not in order:
            block("buys must be market orders sized with dollar_amount")
        amt = float(order["dollar_amount"])
        if amt > cfg["max_order_usd"]:
            block(f"dollar_amount {amt} exceeds max_order_usd {cfg['max_order_usd']}")
    elif side == "sell":
        if "quantity" not in order:
            block("sells must specify quantity (full position)")
    else:
        block(f"unknown side {side!r}")

    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() > 4 or not (9 <= now.hour < 16):
        block(f"outside regular market hours ({now:%a %H:%M} ET)")

    last = state.get("last_action") or {}
    if last.get("date") == str(now.date()) and last.get("order_placed"):
        block("an order was already placed today (max 1/day)")

    sys.exit(0)


if __name__ == "__main__":
    main()
