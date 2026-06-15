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
from typing import Any
from zoneinfo import ZoneInfo

from market_calendar import early_close_minutes, is_market_holiday

# Test seam: ORDER_GATE_ROOT overrides the repo root (unset in production).
ROOT = Path(os.environ.get("ORDER_GATE_ROOT") or Path(__file__).parent.parent)
ET = ZoneInfo("America/New_York")
DEFAULT_PRICE_TOLERANCE_PCT = 2.0
DEFAULT_QUOTE_MAX_AGE_SEC = 15 * 60
ORDER_PRICE_FIELDS = (
    "limit_price",
    "stop_price",
    "price",
    "estimated_price",
    "expected_price",
    "implied_price",
    "estimated_unit_price",
    "quote_price",
)


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


def config_float(cfg: dict, key: str, default: float) -> float:
    try:
        value = float(cfg.get(key, default))
    except (TypeError, ValueError):
        block(f"{key} must be numeric")
    if value <= 0:
        block(f"{key} must be > 0")
    return value


def parse_positive_float(value: Any, name: str) -> float:
    try:
        number = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        block(f"{name} {value!r} is not a number")
    if number <= 0:
        block(f"{name} {number!r} must be > 0")
    return number


def parse_quote_ts(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        block("last_quote.ts missing or not a timestamp")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        ts = datetime.fromisoformat(raw)
    except ValueError:
        block(f"last_quote.ts {value!r} is not ISO-8601")
    if ts.tzinfo is not None:
        ts = ts.astimezone(ET).replace(tzinfo=None)
    return ts


def et_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(ET).replace(tzinfo=None)
    return value


def order_reference_price(order: dict) -> float | None:
    for field in ORDER_PRICE_FIELDS:
        value = order.get(field)
        if value not in (None, ""):
            return parse_positive_float(value, field)
    if order.get("dollar_amount") not in (None, "") and order.get("quantity") not in (None, ""):
        dollars = parse_positive_float(order.get("dollar_amount"), "dollar_amount")
        qty = parse_positive_float(order.get("quantity"), "quantity")
        return dollars / qty
    return None


def validate_quote_freshness_and_price(cfg: dict, state: dict, order: dict, now: datetime) -> None:
    quote = state.get("last_quote")
    if not isinstance(quote, dict):
        block("last_quote missing from state/state.json — run scripts/decide_with_quote.py before placing orders")

    quote_symbol = quote.get("symbol")
    if not isinstance(quote_symbol, str) or quote_symbol.upper() != str(cfg["symbol"]).upper():
        block(f"last_quote symbol {quote_symbol!r} does not match configured symbol {cfg['symbol']}")

    quote_price = parse_positive_float(quote.get("price"), "last_quote.price")
    quote_ts = parse_quote_ts(quote.get("ts"))
    max_age_sec = config_float(cfg, "quote_max_age_sec", DEFAULT_QUOTE_MAX_AGE_SEC)
    age_sec = (et_naive(now) - quote_ts).total_seconds()
    if age_sec < 0:
        block(f"last_quote timestamp {quote.get('ts')!r} is in the future")
    if age_sec > max_age_sec:
        block(f"last_quote is stale ({age_sec:.0f}s old; max {max_age_sec:.0f}s)")

    order_price = order_reference_price(order)
    if order_price is None:
        return
    tolerance_pct = config_float(cfg, "price_tolerance_pct", DEFAULT_PRICE_TOLERANCE_PCT)
    deviation_pct = abs(order_price - quote_price) / quote_price * 100
    if deviation_pct > tolerance_pct:
        block(
            f"order price {order_price:.4f} deviates {deviation_pct:.2f}% from "
            f"last_quote {quote_price:.4f} (max {tolerance_pct:.2f}%)"
        )


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


def write_marker(today: str, order: dict, allowed_at: datetime) -> None:
    marker = {
        "date": today,
        "allowed_at": allowed_at.isoformat(),
        "ref_id": order.get("ref_id"),
        "account_number": order.get("account_number"),
        "symbol": order.get("symbol"),
        "side": order.get("side"),
        "type": order.get("type"),
    }
    if "dollar_amount" in order:
        marker["dollar_amount"] = order.get("dollar_amount")
    if "quantity" in order:
        marker["quantity"] = order.get("quantity")
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(marker, indent=2) + "\n")


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
    ref_id = order.get("ref_id")
    if ref_id is None or not str(ref_id).strip():
        block("ref_id is required and must be non-empty")

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
    validate_quote_freshness_and_price(cfg, state, order, now)
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
    last = state.get("last_action") or {}
    seeded = last.get("date") == today and last.get("order_placed")
    if marker_blocks_today(today) or seeded:
        block("an order was already placed today (max 1/day)")

    # Allow path: record the gate's own marker before exiting so the next
    # invocation today is capped without any model write to state.
    write_marker(today, order, now)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # fail closed: a crashing gate must block, never allow
        block(f"gate error ({type(exc).__name__}: {exc})")
