#!/usr/bin/env python3
"""PreToolUse hook: deterministic gate on place_option_order.

Same trust model as scripts/order_gate.py: exit 2 blocks, exit 0 allows,
fail closed on any unreadable config/state. Options-specific rules:

- long-only: a leg must be buy+open (new long) or sell+close (close a long).
  sell+open (short premium) and anything else is blocked outright.
- opens must be limit orders with a price; premium (price x qty x 100) is
  capped by max_option_premium_usd, contracts by max_option_contracts.
- at most one option order per day (state last_option_action).

Known limit: the order payload carries only the option instrument UUID, so
the underlying symbol cannot be verified here without a network call. The
premium cap, long-only rule, and the dedicated account are the backstops.
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Test seam: ORDER_GATE_ROOT overrides the repo root (unset in production).
ROOT = Path(os.environ.get("ORDER_GATE_ROOT") or Path(__file__).parent.parent)


def now_et() -> datetime:
    """Current time in ET. Test seam: ORDER_GATE_NOW (ISO 8601) overrides."""
    override = os.environ.get("ORDER_GATE_NOW")
    if override:
        return datetime.fromisoformat(override)
    return datetime.now(ZoneInfo("America/New_York"))


def block(msg: str) -> None:
    print(f"ORDER BLOCKED: {msg}", file=sys.stderr)
    sys.exit(2)


def deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(root: Path = ROOT) -> dict:
    """Tracked config.json deep-merged with untracked config.local.json."""
    cfg = json.loads((root / "config.json").read_text())
    local = root / "config.local.json"
    if local.exists():
        cfg = deep_merge(cfg, json.loads(local.read_text()))
    return cfg


def main() -> None:
    payload = json.load(sys.stdin)
    if not payload.get("tool_name", "").endswith("place_option_order"):
        sys.exit(0)

    try:
        cfg = load_config()
        state = json.loads((ROOT / "state" / "state.json").read_text())
    except Exception as exc:  # fail closed: a broken config must never allow
        block(f"cannot load config/state ({type(exc).__name__}: {exc})")
    order = payload.get("tool_input", {})

    if not (ROOT / "config.local.json").exists():
        block(
            "config.local.json is missing — create it next to config.json "
            'with {"account_number": "<real account>"}; no orders without it'
        )
    account = cfg.get("account_number")
    if not account or account == "REPLACE_ME":
        block(
            "account_number is missing or still the REPLACE_ME placeholder — "
            "set the real value in config.local.json"
        )

    if cfg.get("dry_run"):
        block("dry_run=true in config.json — review only, no live orders")
    if state.get("halt"):
        block(f"kill switch active: {state.get('halt_reason')}")
    if order.get("account_number") != account:
        block(f"account {order.get('account_number')!r} is not the agentic account")

    max_premium = cfg.get("max_option_premium_usd")
    max_contracts = cfg.get("max_option_contracts")
    if not max_premium or not max_contracts:
        block("max_option_premium_usd / max_option_contracts not configured — "
              "options trading is not enabled in config.json")

    legs = order.get("legs") or []
    if len(legs) != 1:
        block(f"exactly one leg required, got {len(legs)}")
    leg = legs[0]
    side = (leg.get("side"), leg.get("position_effect"))
    if side not in {("buy", "open"), ("sell", "close")}:
        block(f"leg {side!r} not allowed: only buy+open or sell+close "
              "(long-only, never short premium)")
    if leg.get("ratio_quantity") not in (None, 1):
        block(f"ratio_quantity {leg.get('ratio_quantity')!r} must be 1")

    try:
        qty = int(order.get("quantity"))
    except (TypeError, ValueError):
        block(f"quantity {order.get('quantity')!r} is not an integer")
    if qty < 1:
        block(f"quantity {qty} must be >= 1")
    if qty > max_contracts:
        block(f"quantity {qty} exceeds max_option_contracts {max_contracts}")

    if side == ("buy", "open"):
        if order.get("type", "limit") != "limit" or not order.get("price"):
            block("opens must be limit orders with a price")
        premium = float(order["price"]) * qty * 100
        if premium > max_premium:
            block(f"premium ${premium:.2f} (price x qty x 100) exceeds "
                  f"max_option_premium_usd {max_premium}")

    now = now_et()
    if now.weekday() > 4 or not (9 <= now.hour < 16):
        block(f"outside regular market hours ({now:%a %H:%M} ET)")

    last = state.get("last_option_action") or {}
    if last.get("date") == str(now.date()) and last.get("order_placed"):
        block("an option order was already placed today (max 1/day)")

    sys.exit(0)


if __name__ == "__main__":
    main()
