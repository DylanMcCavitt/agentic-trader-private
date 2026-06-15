#!/usr/bin/env python3
"""Persist the broker quote used for a decision, then run decide.py.

The order gate cannot call the broker MCP, so this wrapper records the quote
(price + timestamp) at decision time under state/state.json as last_quote.
The gate later validates any order against that persisted quote.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

import decide

ROOT = Path(__file__).parent.parent
ET = ZoneInfo("America/New_York")
PRICE_KEYS = (
    "last_trade_price",
    "last_price",
    "regular_market_price",
    "regularMarketPrice",
    "mark_price",
    "price",
)
TS_KEYS = (
    "last_trade_at",
    "last_trade_time",
    "last_trade_timestamp",
    "last_trade_ts",
    "regular_market_time",
    "regularMarketTime",
    "updated_at",
    "timestamp",
    "ts",
)
SYMBOL_KEYS = ("symbol", "ticker")
WRAPPER_KEYS = {"quote", "quotes", "result", "results", "data", "items", "payload", "response"}


def parse_price(value: Any) -> float:
    if isinstance(value, (int, float)):
        price = float(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if cleaned.startswith("$"):
            cleaned = cleaned[1:]
        price = float(cleaned)
    else:
        raise ValueError(f"unsupported price value {value!r}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price!r}")
    return price


def parse_timestamp(value: Any) -> str:
    """Return an ISO-8601 timestamp string, preserving offsets when present."""
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:  # milliseconds since epoch
            ts /= 1000
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).isoformat()
    if not isinstance(value, str):
        raise ValueError(f"unsupported timestamp value {value!r}")
    raw = value.strip()
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return parse_timestamp(float(raw))
    if raw.endswith("Z"):
        dt = datetime.fromisoformat(raw[:-1] + "+00:00")
        return dt.isoformat()
    # Validate that the timestamp is parseable, but keep the broker's spelling.
    datetime.fromisoformat(raw)
    return raw


def symbol_from(obj: dict[str, Any]) -> str | None:
    for key in SYMBOL_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().upper()
    return None


def symbol_key(key: Any) -> str | None:
    if not isinstance(key, str):
        return None
    cleaned = key.strip()
    if cleaned.lower() in WRAPPER_KEYS:
        return None
    if re.fullmatch(r"[A-Za-z][A-Za-z.]{0,5}", cleaned):
        return cleaned.upper()
    return None


def iter_quote_candidates(obj: Any, inherited_symbol: str | None = None) -> Iterator[dict[str, Any]]:
    """Yield dictionaries that may be quote objects from flexible MCP shapes."""
    if isinstance(obj, dict):
        current_symbol = symbol_from(obj) or inherited_symbol
        if any(key in obj for key in PRICE_KEYS) and any(key in obj for key in TS_KEYS):
            candidate = dict(obj)
            if current_symbol and not symbol_from(candidate):
                candidate["symbol"] = current_symbol
            yield candidate
        for key, value in obj.items():
            yield from iter_quote_candidates(value, symbol_key(key) or current_symbol)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_quote_candidates(item, inherited_symbol)


def extract_quote(payload: Any, symbol: str) -> dict[str, Any]:
    wanted = symbol.upper()
    candidates = list(iter_quote_candidates(payload))
    matching = [q for q in candidates if symbol_from(q) in (wanted, None)]
    if not matching:
        raise ValueError(f"no quote for {wanted} with price and timestamp found")

    quote = matching[0]
    price_value = next((quote[key] for key in PRICE_KEYS if quote.get(key) not in (None, "")), None)
    ts_value = next((quote[key] for key in TS_KEYS if quote.get(key) not in (None, "")), None)
    if price_value is None or ts_value is None:
        raise ValueError(f"quote for {wanted} missing price or timestamp")

    return {"symbol": wanted, "price": parse_price(price_value), "ts": parse_timestamp(ts_value)}


def load_quote_payload(args: argparse.Namespace) -> Any:
    if args.quote_file:
        return json.loads(Path(args.quote_file).read_text())
    if args.quote_json:
        return json.loads(args.quote_json)
    return None


def persist_last_quote(root: Path, quote: dict[str, Any]) -> None:
    state_path = root / "state" / "state.json"
    if not state_path.exists():
        raise FileNotFoundError("state/state.json is missing")
    state = json.loads(state_path.read_text())
    if not isinstance(state, dict):
        raise ValueError("state/state.json must contain a JSON object")
    state["last_quote"] = quote
    tmp_path = state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, indent=2) + "\n")
    tmp_path.replace(state_path)


def main() -> None:
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--quote-json", help="raw JSON quote payload from the broker MCP")
    source.add_argument("--quote-file", help="path to raw JSON quote payload from the broker MCP")
    ap.add_argument("--price", type=float, help="broker last-trade price (fallback when raw quote JSON is unavailable)")
    ap.add_argument("--quote-ts", help="broker quote/last-trade timestamp (required with --price)")
    ap.add_argument("--holding", choices=["true", "false"], required=True)
    args = ap.parse_args()

    symbol = decide.CONFIG["symbol"]
    try:
        payload = load_quote_payload(args)
        if payload is not None:
            quote = extract_quote(payload, symbol)
        elif args.price is not None and args.quote_ts:
            quote = {"symbol": symbol, "price": parse_price(args.price), "ts": parse_timestamp(args.quote_ts)}
        else:
            ap.error("provide --quote-json/--quote-file, or both --price and --quote-ts")
        decision = decide.compute_decision(quote["price"], args.holding == "true")
        persist_last_quote(ROOT, quote)
    except Exception as exc:
        print(json.dumps({"decision": "ERROR", "reason": f"quote wrapper failed: {exc}"}))
        sys.exit(1)

    print(json.dumps(decision))


if __name__ == "__main__":
    main()
