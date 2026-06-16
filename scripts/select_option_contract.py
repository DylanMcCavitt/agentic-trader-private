#!/usr/bin/env python3
"""Pick a tradeable option contract from the broker's chain, under a premium budget.

Usage:
  uv run scripts/select_option_contract.py --right call --spot 207.50 \
      --dte-min 28 --dte-max 45 --max-premium 300 --contracts 1 \
      --chains-json '<raw get_option_instruments + get_option_quotes JSON>'
  (or --chains-file PATH, or pipe the JSON on stdin)

Live contract selection for the option sleeve. Unlike the paper engine
(scripts/strategies/contracts.py, which reads yfinance chains), this picks
from the *broker's* chain so the order references a real tradable instrument
id that place_option_order / the option gate can act on.

Selection (calls and puts both): among contracts of the requested right whose
expiration falls in [today+dte_min, today+dte_max], take the nearest expiry,
then -- NOT necessarily in the money -- the highest-premium contract whose
1-lot cost (price x 100 x contracts) is still <= max_premium. That is the most
meaningful (highest-delta) contract affordable under the budget. Price is the
ask (a marketable buy limit), falling back to mark then bid. Prints the chosen
contract + limit price, or within_budget=false with the cheapest contract seen
so the journal can explain why nothing was placed.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterator

WRAPPER_KEYS = ("results", "result", "data", "items", "option_chains",
                "contracts", "instruments", "options", "payload", "response")
OPTION_ID_KEYS = ("option_id", "optionId", "id", "instrument_id", "instrumentId")
EXPIRY_KEYS = ("expiration_date", "expirationDate", "expiry", "expiration")
STRIKE_KEYS = ("strike_price", "strikePrice", "strike")
RIGHT_KEYS = ("type", "right", "option_type", "optionType")
SYMBOL_KEYS = ("chain_symbol", "chainSymbol", "underlying_symbol",
               "underlyingSymbol", "symbol")
ASK_KEYS = ("ask_price", "askPrice", "ask")
BID_KEYS = ("bid_price", "bidPrice", "bid")
MARK_KEYS = ("adjusted_mark_price", "adjustedMarkPrice", "mark_price",
             "markPrice", "mark", "last_trade_price", "lastTradePrice")
CONTRACT_MARKERS = EXPIRY_KEYS + STRIKE_KEYS  # a dict that looks like a contract


def _first(obj: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return value
    return None


def _num(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").lstrip("$")
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def iter_contracts(obj: Any) -> Iterator[dict]:
    """Yield dicts that look like option contracts from flexible broker shapes:
    a dict with both an expiry and a strike, or any contract nested under the
    common wrapper keys / lists."""
    if isinstance(obj, dict):
        if any(k in obj for k in EXPIRY_KEYS) and any(k in obj for k in STRIKE_KEYS):
            yield obj
        for value in obj.values():
            yield from iter_contracts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from iter_contracts(item)


def normalize(raw: dict) -> dict | None:
    """Pull the fields the selector needs out of one broker contract dict."""
    expiry = _first(raw, EXPIRY_KEYS)
    strike = _num(_first(raw, STRIKE_KEYS))
    right = _first(raw, RIGHT_KEYS)
    if expiry is None or strike is None or not isinstance(right, str):
        return None
    try:
        exp_date = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return None
    ask = _num(_first(raw, ASK_KEYS))
    mark = _num(_first(raw, MARK_KEYS))
    bid = _num(_first(raw, BID_KEYS))
    # marketable buy limit: prefer ask, then mark, then bid
    price = next((p for p in (ask, mark, bid) if p is not None and p > 0), None)
    return {"option_id": _first(raw, OPTION_ID_KEYS),
            "symbol": _first(raw, SYMBOL_KEYS),
            "expiry": exp_date.isoformat(), "expiry_date": exp_date,
            "strike": strike, "right": right.strip().lower(),
            "price": price, "ask": ask, "mark": mark, "bid": bid}


def select_contract(raw: Any, *, right: str, spot: float, dte_min: int,
                    dte_max: int, max_premium: float, contracts: int = 1,
                    today: date | None = None) -> dict:
    today = today or date.today()
    right = right.strip().lower()
    parsed = [c for c in (normalize(r) for r in iter_contracts(raw)) if c]
    in_window = [
        c for c in parsed
        if c["right"] == right
        and dte_min <= (c["expiry_date"] - today).days <= dte_max
    ]
    if not in_window:
        return {"within_budget": False, "contract": None,
                "reason": f"no {right} contracts with expiry in "
                          f"[{dte_min},{dte_max}] DTE among {len(parsed)} parsed"}

    nearest = min(c["expiry_date"] for c in in_window)
    at_expiry = [c for c in in_window if c["expiry_date"] == nearest]
    priced = [c for c in at_expiry if c["price"] is not None and c["price"] > 0]
    if not priced:
        return {"within_budget": False, "contract": None,
                "reason": f"no priced {right} contracts at nearest expiry "
                          f"{nearest.isoformat()}"}

    for c in priced:
        c["premium"] = round(c["price"] * 100 * contracts, 2)
    affordable = [c for c in priced if c["premium"] <= max_premium]
    dte = (nearest - today).days

    if affordable:
        # highest premium under budget == closest-to-money == highest delta
        chosen = max(affordable, key=lambda c: (c["premium"], -c["strike"]))
        return {"within_budget": True,
                "option_id": chosen["option_id"], "symbol": chosen["symbol"],
                "right": right, "expiry": chosen["expiry"], "dte": dte,
                "strike": chosen["strike"], "limit_price": round(chosen["price"], 2),
                "premium": chosen["premium"], "contracts": contracts,
                "reason": (f"{right} {chosen['strike']:g} exp {chosen['expiry']} "
                           f"({dte} DTE) @ {chosen['price']:.2f} -> "
                           f"premium ${chosen['premium']:.2f} <= ${max_premium:g} budget")}

    cheapest = min(priced, key=lambda c: c["premium"])
    return {"within_budget": False, "contract": None,
            "right": right, "expiry": cheapest["expiry"], "dte": dte,
            "cheapest_strike": cheapest["strike"],
            "cheapest_premium": cheapest["premium"], "contracts": contracts,
            "reason": (f"cheapest {right} at {cheapest['expiry']} is "
                       f"${cheapest['premium']:.2f} (strike {cheapest['strike']:g}), "
                       f"exceeds ${max_premium:g} budget")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--right", choices=["call", "put"], required=True)
    ap.add_argument("--spot", type=float, required=True)
    ap.add_argument("--dte-min", type=int, required=True)
    ap.add_argument("--dte-max", type=int, required=True)
    ap.add_argument("--max-premium", type=float, required=True,
                    help="premium cap in dollars (config max_option_premium_usd)")
    ap.add_argument("--contracts", type=int, default=1)
    ap.add_argument("--today", help="override today (tests only)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--chains-json", help="raw broker chain + quotes JSON")
    src.add_argument("--chains-file", help="path to raw broker chain + quotes JSON")
    args = ap.parse_args()

    try:
        if args.chains_file:
            text = Path(args.chains_file).read_text()
        elif args.chains_json:
            text = args.chains_json
        else:
            text = sys.stdin.read()
        raw = json.loads(text)
        today = date.fromisoformat(args.today) if args.today else date.today()
        result = select_contract(raw, right=args.right, spot=args.spot,
                                 dte_min=args.dte_min, dte_max=args.dte_max,
                                 max_premium=args.max_premium,
                                 contracts=args.contracts, today=today)
    except Exception as exc:
        print(json.dumps({"within_budget": False, "contract": None,
                          "reason": f"contract selection failed: {exc}"}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
