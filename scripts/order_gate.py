#!/usr/bin/env python3
"""PreToolUse hook: deterministic gate on place_equity_order.

Reads the hook payload from stdin. Exit 2 blocks the order (stderr is shown
to the model); exit 0 allows it. This gate, not the model, is the real
guardrail — keep every rule here mechanical.
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
    if not payload.get("tool_name", "").endswith("place_equity_order"):
        sys.exit(0)

    try:
        cfg = load_config()
        state = json.loads((ROOT / "state" / "state.json").read_text())
    except Exception as exc:  # fail closed: a broken config must never allow
        block(f"cannot load config/state ({type(exc).__name__}: {exc})")
    order = payload.get("tool_input", {})

    # Guardrail: the real account number lives only in untracked
    # config.local.json. Missing file or placeholder value = hard block.
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

    now = now_et()
    minutes = now.hour * 60 + now.minute
    if now.weekday() > 4 or not (9 * 60 + 30 <= minutes < 16 * 60):
        block(f"outside regular market hours ({now:%a %H:%M} ET)")

    last = state.get("last_action") or {}
    if last.get("date") == str(now.date()) and last.get("order_placed"):
        block("an order was already placed today (max 1/day)")

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a crashing gate must block, never allow
        block(f"gate error ({type(exc).__name__}: {exc})")
