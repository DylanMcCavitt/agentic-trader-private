#!/usr/bin/env python3
"""PreToolUse hook: deterministic gate on place_option_order.

Same trust model as scripts/order_gate.py: exit 2 blocks, exit 0 allows,
fail closed on any unreadable config/state. Options-specific rules:

- long-only: a leg must be buy+open (new long) or sell+close (close a long).
  sell+open (short premium) and anything else is blocked outright.
- opens must be limit orders with a price; premium (price x qty x 100) is
  capped by max_option_premium_usd, contracts by max_option_contracts.
- at most one option order per day: on allow, the gate records the attempt
  in its own marker file state/gate_option.json (a pre-tool-use record — it
  counts allowed attempts, not fills, which errs on the side of blocking).
  The marker is gate-owned and separate from state.json, so the cap cannot
  depend on the model honestly recording its own permission.

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

from market_calendar import early_close_minutes, is_market_holiday

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


# Gate-owned marker: records "an option order was allowed today (ET date)".
# Its own file (separate from the equity marker and from state.json) so the
# option cap is independent of the equity cap and of any model write.
MARKER = ROOT / "state" / "gate_option.json"


def marker_blocks_today(today: str) -> bool:
    """True if the gate's own marker shows an option order was allowed today.

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
    if not payload.get("tool_name", "").endswith("place_option_order"):
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
    required = ("symbol", "max_order_usd", "account_number", "dry_run",
                "max_option_premium_usd", "max_option_contracts")
    missing = [k for k in required if k not in cfg]
    if missing:
        block(f"config is missing required key(s): {', '.join(missing)}")

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
        try:
            price = float(order["price"])
        except (TypeError, ValueError):
            block(f"price {order.get('price')!r} is not numeric")
        if price <= 0:
            block(f"price {price} must be > 0")
        premium = price * qty * 100
        if premium > max_premium:
            block(f"premium ${premium:.2f} (price x qty x 100) exceeds "
                  f"max_option_premium_usd {max_premium}")

    now = now_et()
    if is_market_holiday(now.date()):
        block(f"US market holiday ({now.date()})")
    minutes = now.hour * 60 + now.minute
    # Half-days close early (13:00 ET); use that as the session upper bound.
    close = early_close_minutes(now.date()) or (16 * 60)
    if now.weekday() > 4 or not (9 * 60 + 30 <= minutes < close):
        block(f"outside regular market hours ({now:%a %H:%M} ET)")

    today = str(now.date())
    # Gate-owned marker is the primary cap (model can't clobber it). The
    # state.json read is kept as a secondary, back-compat block condition.
    last = state.get("last_option_action") or {}
    seeded = last.get("date") == today and last.get("order_placed")
    if marker_blocks_today(today) or seeded:
        block("an option order was already placed today (max 1/day)")

    # Allow path: record the gate's own marker (replaces the old write into
    # state.json last_option_action, which the model could clobber).
    write_marker(today)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a crashing gate must block, never allow
        block(f"gate error ({type(exc).__name__}: {exc})")
