#!/usr/bin/env python3
"""Deterministically reconcile state/state.json from broker order lists.

The model may describe what it believes happened in the journal, but this
script owns the canonical state fields consumed by safety checks. It matches a
broker order list against the gate-owned marker when possible, then writes
last_action / last_option_action from broker data only.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Test seam: RECONCILE_STATE_ROOT overrides the repo root (unset in production).
ROOT = Path(os.environ.get("RECONCILE_STATE_ROOT") or Path(__file__).parent.parent)
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def now_et() -> datetime:
    """Current time in ET. Test seam: RECONCILE_STATE_NOW overrides."""
    override = os.environ.get("RECONCILE_STATE_NOW")
    if override:
        return datetime.fromisoformat(override)
    return datetime.now(ZoneInfo("America/New_York"))


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w") as tmp:
            json.dump(data, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(path.parent, os.O_DIRECTORY)
        except (AttributeError, OSError):
            return
        try:
            try:
                os.fsync(dir_fd)
            except OSError:
                pass
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def load_json_text(text: str, name: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc


def extract_orders(raw: Any) -> list[dict[str, Any]]:
    """Accept common broker/MCP response shapes and return order dictionaries."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        for key in (
            "orders",
            "results",
            "result",
            "data",
            "items",
            "equity_orders",
            "option_orders",
        ):
            value = raw.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = extract_orders(value)
                if nested:
                    return nested
        order = raw.get("order")
        if isinstance(order, dict):
            return [order]
        if any(key in raw for key in ("id", "order_id", "orderId", "state", "status")):
            return [raw]
    return []


def load_marker(kind: str, target_date: str, root: Path) -> dict[str, Any] | None:
    path = (root / "state" / "gate_equity.json") if kind == "equity" else (
        root / "state" / "gate_option.json"
    )
    if not path.exists():
        return None
    try:
        marker = json.loads(path.read_text())
    except Exception:
        return None
    if isinstance(marker, dict) and marker.get("date") == target_date:
        return marker
    return None


def nested_get(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def first_value(order: dict[str, Any], paths: tuple[str, ...]) -> Any:
    for path in paths:
        value = nested_get(order, path)
        if value not in (None, ""):
            return value
    return None


def as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    return text if text.strip() else None


def normalize_ref(value: Any) -> str | None:
    text = as_text(value)
    return text.strip().lower() if text is not None else None


def order_id(order: dict[str, Any]) -> str | None:
    return as_text(first_value(order, ("id", "order_id", "orderId", "uuid")))


def order_ref_id(order: dict[str, Any]) -> str | None:
    for path in (
        "ref_id",
        "refId",
        "client_order_id",
        "clientOrderId",
        "client_id",
        "clientId",
    ):
        value = as_text(nested_get(order, path))
        if value is not None:
            return value
    return None


def order_status(order: dict[str, Any]) -> str | None:
    return as_text(first_value(order, ("state", "status", "order_state", "orderState")))


def order_symbol(order: dict[str, Any]) -> str | None:
    return as_text(
        first_value(
            order,
            (
                "symbol",
                "instrument.symbol",
                "instrument_symbol",
                "chain_symbol",
                "underlying_symbol",
            ),
        )
    )


def order_side(order: dict[str, Any]) -> str | None:
    side = as_text(first_value(order, ("side", "direction")))
    if side:
        return side.lower()
    legs = order.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return as_text(legs[0].get("side"))
    return None


def order_position_effect(order: dict[str, Any]) -> str | None:
    effect = as_text(first_value(order, ("position_effect", "positionEffect")))
    if effect:
        return effect.lower()
    legs = order.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return as_text(legs[0].get("position_effect") or legs[0].get("positionEffect"))
    return None


def option_id(order: dict[str, Any]) -> str | None:
    value = first_value(order, ("option_id", "optionId", "option"))
    if value not in (None, ""):
        return str(value)
    legs = order.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return as_text(
            legs[0].get("option_id") or legs[0].get("optionId") or legs[0].get("option")
        )
    return None


def order_date(order: dict[str, Any]) -> str | None:
    for path in (
        "created_at",
        "createdAt",
        "submitted_at",
        "submittedAt",
        "queued_at",
        "queuedAt",
        "updated_at",
        "updatedAt",
        "last_transaction_at",
        "lastTransactionAt",
        "executed_at",
        "executedAt",
        "date",
    ):
        value = first_value(order, (path,))
        if value in (None, ""):
            continue
        match = DATE_RE.search(str(value))
        if match:
            return match.group(1)
    return None


def quantity_value(order: dict[str, Any]) -> Any:
    return first_value(order, ("quantity", "requested_quantity", "requestedQuantity"))


def filled_quantity_value(order: dict[str, Any]) -> Any:
    return first_value(
        order,
        (
            "filled_quantity",
            "filledQuantity",
            "cumulative_quantity",
            "cumulativeQuantity",
            "processed_quantity",
            "processedQuantity",
            "executed_quantity",
            "executedQuantity",
        ),
    )


def average_price_value(order: dict[str, Any]) -> Any:
    return first_value(
        order,
        ("average_price", "averagePrice", "average_fill_price", "averageFillPrice"),
    )


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def is_filled(order: dict[str, Any]) -> bool:
    status = (order_status(order) or "").lower().replace("-", "_")
    if status in {"filled", "executed", "complete", "completed"}:
        return True
    qty = decimal_or_none(quantity_value(order))
    filled = decimal_or_none(filled_quantity_value(order))
    return bool(qty is not None and filled is not None and qty > 0 and filled >= qty)


def date_compatible(order: dict[str, Any], target_date: str) -> bool:
    seen = order_date(order)
    return seen in (None, target_date)


def sort_key(order: dict[str, Any]) -> str:
    for path in (
        "updated_at",
        "updatedAt",
        "last_transaction_at",
        "lastTransactionAt",
        "created_at",
        "createdAt",
        "submitted_at",
        "submittedAt",
        "date",
    ):
        value = first_value(order, (path,))
        if value not in (None, ""):
            return str(value)
    return ""


def find_matching_order(
    orders: list[dict[str, Any]],
    *,
    target_date: str,
    marker: dict[str, Any] | None,
) -> dict[str, Any] | None:
    # The gate marker is the only trusted join key between this run's placement
    # attempt and the broker list. Without a same-day, non-empty ref_id, do not
    # infer a match from symbol/side/account metadata.
    if not marker or marker.get("date") != target_date:
        return None
    ref_id = normalize_ref(marker.get("ref_id"))
    if not ref_id:
        return None

    exact = [
        order
        for order in orders
        if normalize_ref(order_ref_id(order)) == ref_id
        and date_compatible(order, target_date)
    ]
    if not exact:
        return None
    return sorted(exact, key=sort_key, reverse=True)[0]


def load_config_symbol(root: Path) -> str | None:
    try:
        cfg = json.loads((root / "config.json").read_text())
    except Exception:
        return None
    symbol = cfg.get("symbol") if isinstance(cfg, dict) else None
    return str(symbol) if symbol not in (None, "") else None


def build_action_record(
    *,
    kind: str,
    target_date: str,
    decision: str | None,
    action: str | None,
    matched: dict[str, Any] | None,
    marker: dict[str, Any] | None,
    symbol: str | None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"date": target_date}
    if decision is not None:
        record["decision"] = decision
    if action is not None:
        record["action"] = action

    if matched is None:
        record.update(
            {
                "order_placed": False,
                "order_id": None,
                "status": "not_found",
                "fill_state": None,
            }
        )
        record["filled"] = False
        marker_ref_id = as_text(marker.get("ref_id")) if marker else None
        if marker_ref_id:
            record["ref_id"] = marker_ref_id
        if marker and marker.get("side"):
            record["side"] = marker.get("side")
        if marker and marker.get("symbol"):
            record["symbol"] = marker.get("symbol")
        elif symbol and kind == "equity":
            record["symbol"] = symbol
        return record

    status = order_status(matched) or "unknown"
    filled = is_filled(matched)
    record.update(
        {
            "order_placed": True,
            "order_id": order_id(matched),
            "status": status,
            "fill_state": status,
            "filled": filled,
        }
    )
    ref_id = order_ref_id(matched) or (marker or {}).get("ref_id")
    if ref_id:
        record["ref_id"] = ref_id
    side = order_side(matched) or (marker or {}).get("side")
    if side:
        record["side"] = side
    resolved_symbol = order_symbol(matched) or (marker or {}).get("symbol") or symbol
    if resolved_symbol:
        record["symbol"] = resolved_symbol
    effect = order_position_effect(matched) or (marker or {}).get("position_effect")
    if kind == "option" and effect:
        record["position_effect"] = effect
    filled_qty = filled_quantity_value(matched)
    if filled_qty not in (None, ""):
        record["filled_quantity"] = filled_qty
    avg_price = average_price_value(matched)
    if avg_price not in (None, ""):
        record["average_price"] = avg_price
    return record


def reconcile_state(
    *,
    kind: str,
    orders_raw: Any,
    root: Path = ROOT,
    target_date: str | None = None,
    decision: str | None = None,
    action: str | None = None,
    symbol: str | None = None,
    last_run: str | None = None,
) -> dict[str, Any]:
    if kind not in {"equity", "option"}:
        raise ValueError("kind must be 'equity' or 'option'")

    now = now_et()
    target_date = target_date or str(now.date())
    state_path = root / "state" / "state.json"
    state = json.loads(state_path.read_text())
    if not isinstance(state, dict):
        raise ValueError("state/state.json must contain a JSON object")

    marker = load_marker(kind, target_date, root)
    symbol = symbol or (marker or {}).get("symbol")
    if symbol is None and kind == "equity":
        symbol = load_config_symbol(root)
    orders = extract_orders(orders_raw)
    matched = find_matching_order(
        orders,
        target_date=target_date,
        marker=marker,
    )
    record = build_action_record(
        kind=kind,
        target_date=target_date,
        decision=decision,
        action=action,
        matched=matched,
        marker=marker,
        symbol=symbol,
    )

    state["last_run"] = last_run or now.isoformat()
    key = "last_action" if kind == "equity" else "last_option_action"
    state[key] = record

    if kind == "equity" and matched is not None and is_filled(matched):
        side = (record.get("side") or "").lower()
        if side == "buy":
            state["position_opened"] = target_date
        elif side == "sell":
            state["position_opened"] = None

    atomic_write_json(state_path, state)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["equity", "option"], required=True)
    parser.add_argument("--date", dest="target_date", help="ET date YYYY-MM-DD")
    parser.add_argument("--decision", help="Strategy decision to copy into state")
    parser.add_argument("--action", help="Option/action label to copy into state")
    parser.add_argument("--symbol", help="Expected symbol when no marker supplies one")
    parser.add_argument(
        "--orders-json",
        help="Raw JSON returned by get_equity_orders/get_option_orders. If omitted, read stdin.",
    )
    parser.add_argument("--last-run", help="ISO timestamp to write to last_run")
    args = parser.parse_args()

    orders_text = args.orders_json if args.orders_json is not None else sys.stdin.read()
    try:
        orders_raw = load_json_text(orders_text, "orders JSON")
        record = reconcile_state(
            kind=args.kind,
            orders_raw=orders_raw,
            target_date=args.target_date,
            decision=args.decision,
            action=args.action,
            symbol=args.symbol,
            last_run=args.last_run,
        )
    except Exception as exc:
        print(f"state reconciliation error: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
