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


# Gate-owned marker: records "an equity order was allowed today (ET date)".
# Separate from state.json (which the model also writes), so the once-per-day
# cap can never depend on the model honestly recording its own permission.
MARKER = ROOT / "state" / "gate_equity.json"


def marker_blocks_today(today: str) -> bool:
    """True if the gate's own marker shows an order was already allowed today.

    Missing marker -> no prior order (normal first run, do NOT fail closed).
    Corrupt/unreadable existing marker -> fail closed (block), per #48.
    """
    if not MARKER.exists():
        return False
    try:
        return json.loads(MARKER.read_text()).get("date") == today
    except Exception as exc:  # corrupt existing marker: fail closed
        block(f"gate marker unreadable ({type(exc).__name__}: {exc})")


def write_marker(today: str) -> None:
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps({"date": today}))


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

    # Fail closed on incomplete config: every required key must be present.
    # Existence, not truthiness — a missing dry_run must block (treating it as
    # "not dry-run -> live" is the fail-open hole this closes), while an
    # explicit dry_run=false is a valid present value handled downstream.
    required = ("symbol", "max_order_usd", "account_number", "dry_run")
    missing = [k for k in required if k not in cfg]
    if missing:
        block(f"config is missing required key(s): {', '.join(missing)}")

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
        try:
            amt = float(order["dollar_amount"])
        except (TypeError, ValueError):
            block(f"dollar_amount {order.get('dollar_amount')!r} is not numeric")
        if amt <= 0:
            block(f"dollar_amount {amt} must be > 0")
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

    today = str(now.date())
    # Gate-owned marker is the primary cap (model can't clobber it). The
    # state.json read is kept as a secondary, back-compat block condition.
    last = state.get("last_action") or {}
    seeded = last.get("date") == today and last.get("order_placed")
    if marker_blocks_today(today) or seeded:
        block("an order was already placed today (max 1/day)")

    # Allow path: record the gate's own marker before exiting so the next
    # invocation today is capped without any model write to state.
    write_marker(today)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a crashing gate must block, never allow
        block(f"gate error ({type(exc).__name__}: {exc})")
