#!/usr/bin/env python3
"""Deterministic OPEN/CLOSE/HOLD/NONE decision for one option strategy.

Usage:
  uv run scripts/decide_option.py --strategy NAME --holding false \
      --price <underlying last trade> [--quote-ts <ts>]
  uv run scripts/decide_option.py --strategy NAME --holding true \
      --price <underlying last trade> --expiry YYYY-MM-DD [--quote-ts <ts>]

The option-sleeve twin of decide.py. It computes the strategy's signal on the
underlying -- live price patched in as today's provisional bar, exactly like
the paper engine (scripts/run_strategies.py) -- and maps it to an option
action:

    flat + entry              -> OPEN   (buy to open a long call/put)
    flat + no entry           -> NONE
    held + (exit OR <=exit_dte to expiry) -> CLOSE
    held + neither            -> HOLD

Direction only. Contract selection is a separate broker-driven step
(scripts/select_option_contract.py); the option gate caps premium/contracts.
The option gate does not consume a persisted quote (unlike the equity gate),
so this script writes no state -- it just prints the decision JSON.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from order_gate import deep_merge
from strategies import SIGNALS
from strategies import contracts as oc
from strategies.common import fetch_history

ROOT = Path(__file__).parent.parent


def load_config(root: Path = ROOT) -> dict:
    cfg = json.loads((root / "config.json").read_text())
    local = root / "config.local.json"
    if local.exists():
        cfg = deep_merge(cfg, json.loads(local.read_text()))
    return cfg


def history_with_price(symbol: str, price: float, today: date) -> pd.DataFrame:
    """Daily history whose last row is today's provisional bar with the live
    price patched in -- identical convention to run_strategies.history_with_today."""
    df = fetch_history(symbol)
    df = df[df.index.date <= today]
    if not len(df):
        raise RuntimeError(f"no history for {symbol}")
    q = float(price)
    if df.index[-1].date() == today:
        df.loc[df.index[-1], "Close"] = q
        df.loc[df.index[-1], "High"] = max(float(df["High"].iloc[-1]), q)
        df.loc[df.index[-1], "Low"] = min(float(df["Low"].iloc[-1]), q)
    else:
        df.loc[pd.Timestamp(today)] = {"Open": q, "High": q, "Low": q,
                                       "Close": q, "Volume": 0}
    return df


def compute_option_decision(spec: dict, price: float, holding: bool,
                            today: date, expiry: str | None = None) -> dict:
    """Map a strategy signal to an option action for a live price + state."""
    if spec.get("kind") != "option":
        raise ValueError(f"strategy is kind {spec.get('kind')!r}, not 'option'")
    params = spec["params"]
    df = history_with_price(spec["symbol"], price, today)
    sig = SIGNALS[spec["signal"]](df, params)
    spot = round(float(df["Close"].iloc[-1]), 2)

    if holding:
        exit_dte = params.get("exit_dte")
        near = bool(expiry and exit_dte is not None
                    and oc.near_expiry({"expiry": expiry}, today, exit_dte))
        if near or sig["exit"]:
            decision = "CLOSE"
            why = (f"<= {exit_dte} DTE to {expiry}" if near else "exit signal")
            reason = f"{why}; {sig['reason']}"
        else:
            decision, reason = "HOLD", f"no exit; {sig['reason']}"
    else:
        if sig["entry"]:
            decision, reason = "OPEN", f"entry signal; {sig['reason']}"
        else:
            decision, reason = "NONE", f"no entry; {sig['reason']}"

    return {"strategy": spec.get("name"), "symbol": spec["symbol"],
            "right": spec["right"], "signal": spec["signal"],
            "decision": decision, "reason": reason, "spot": spot,
            "holding": holding, "expiry": expiry,
            "params": {k: params.get(k) for k in
                       ("dte_min", "dte_max", "exit_dte") if k in params}}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--strategy", required=True, help="option strategy name in config")
    ap.add_argument("--holding", choices=["true", "false"], required=True)
    ap.add_argument("--price", type=float, required=True,
                    help="underlying live/last-trade price (today's provisional close)")
    ap.add_argument("--quote-ts", help="underlying quote timestamp (recorded in output for the journal)")
    ap.add_argument("--expiry", help="open position's expiry YYYY-MM-DD (required to honor exit_dte when holding)")
    ap.add_argument("--date", dest="today", help="override today (tests only)")
    args = ap.parse_args()

    try:
        cfg = load_config()
        spec = (cfg.get("strategies") or {}).get(args.strategy)
        if not isinstance(spec, dict):
            raise ValueError(f"unknown strategy {args.strategy!r}")
        spec = {**spec, "name": args.strategy}
        today = date.fromisoformat(args.today) if args.today else date.today()
        result = compute_option_decision(spec, args.price, args.holding == "true",
                                         today, args.expiry)
        if args.quote_ts:
            result["quote_ts"] = args.quote_ts
    except Exception as exc:
        print(json.dumps({"decision": "ERROR",
                          "reason": f"option decision failed: {exc}"}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
