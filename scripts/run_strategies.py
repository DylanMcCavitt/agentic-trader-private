"""Evaluate the whole strategy fleet and update the paper books.

Usage: uv run scripts/run_strategies.py [--quotes '{"SPY": 600.12, ...}']
                                        [--force] [--date YYYY-MM-DD]

One deterministic pass over every enabled strategy in config.json:
compute its signal, simulate any fill into its paper book in
state/paper.json, mark the book to market, and append a run block to
logs/paper.md. Prints a JSON summary. --quotes supplies live prices from
the broker (preferred); without it, yfinance's latest (delayed) prices are
used. Re-running on the same date is refused unless --force.

This script never places real orders. The live trading path stays
TRADER.md -> decide.py -> the order gates.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import paper
from order_gate import deep_merge
from strategies import SIGNALS, signals
from strategies import contracts as oc
from strategies.common import fetch_history

ROOT = Path(__file__).parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
_LOCAL = ROOT / "config.local.json"
if _LOCAL.exists():
    CONFIG = deep_merge(CONFIG, json.loads(_LOCAL.read_text()))
PAPER_PATH = ROOT / "state" / "paper.json"
LOG_PATH = ROOT / "logs" / "paper.md"


def history_with_today(symbol: str, quotes: dict, today: date) -> pd.DataFrame:
    """Daily history whose last row is today's provisional bar. A broker quote
    overrides the close; without one, yfinance's partial bar (or last close)
    stands in."""
    df = fetch_history(symbol)
    df = df[df.index.date <= today]
    if not len(df):
        raise RuntimeError(f"no history for {symbol}")
    if df.index[-1].date() == today:
        if symbol in quotes:
            q = float(quotes[symbol])
            df.loc[df.index[-1], "Close"] = q
            df.loc[df.index[-1], "High"] = max(float(df["High"].iloc[-1]), q)
            df.loc[df.index[-1], "Low"] = min(float(df["Low"].iloc[-1]), q)
    else:
        q = float(quotes.get(symbol, df["Close"].iloc[-1]))
        df.loc[pd.Timestamp(today)] = {"Open": q, "High": q, "Low": q,
                                       "Close": q, "Volume": 0}
    return df


def settle_premium(pos: dict, df: pd.DataFrame) -> float:
    """Intrinsic value at expiry, using the underlying close on (or last
    before) the expiry date."""
    exp = pd.Timestamp(pos["expiry"])
    closes = df["Close"][df.index <= exp]
    return round(oc.intrinsic(pos, float(closes.iloc[-1])), 2)


def run_equity(name: str, spec: dict, book: dict, df: pd.DataFrame,
               today: str, pcfg: dict) -> dict:
    sig = SIGNALS[spec["signal"]](df, spec["params"])
    price = float(df["Close"].iloc[-1])
    action, detail = "NONE", ""
    if book["position"]:
        if sig["exit"]:
            action = "SELL"
            detail = paper.close_equity(book, price, pcfg["slippage_bps"], today,
                                        "exit signal")
        else:
            action = "HOLD"
    elif sig["entry"]:
        action = "BUY"
        detail = paper.open_equity(book, spec["symbol"], price,
                                   pcfg["slippage_bps"], pcfg["position_fraction"],
                                   today)
    value = paper.mark(book, today, equity_price=price)
    return {"action": action, "detail": detail, "reason": sig["reason"],
            "value": value}


def run_rotation(name: str, spec: dict, book: dict, dfs: dict,
                 today: str, pcfg: dict) -> dict:
    sig = signals.momentum_rotation(dfs, spec["params"])
    target = sig["target"]
    held = book["position"]["symbol"] if book["position"] else None
    prices = {s: float(df["Close"].iloc[-1]) for s, df in dfs.items()}
    action, parts = "NONE", []
    if held and held != target:
        action = "SELL" if target is None else "ROTATE"
        parts.append(paper.close_equity(book, prices[held], pcfg["slippage_bps"],
                                        today, "rotation" if target else "risk-off"))
    if target and book["position"] is None:
        action = action if action == "ROTATE" else "BUY"
        parts.append(paper.open_equity(book, target, prices[target],
                                       pcfg["slippage_bps"],
                                       pcfg["position_fraction"], today))
    elif held and held == target:
        action = "HOLD"
    value = paper.mark(book, today, equity_price=prices.get(
        book["position"]["symbol"]) if book["position"] else None)
    return {"action": action, "detail": "; ".join(parts), "reason": sig["reason"],
            "value": value}


def run_option(name: str, spec: dict, book: dict, df: pd.DataFrame,
               today_d: date, pcfg: dict) -> dict:
    today = str(today_d)
    p = spec["params"]
    sig = SIGNALS[spec["signal"]](df, p)
    spot = float(df["Close"].iloc[-1])
    action, detail, premium = "NONE", "", None
    pos = book["position"]
    if pos:
        if oc.is_expired(pos, today_d):
            premium = settle_premium(pos, df)
            action = "SETTLE"
            detail = paper.close_option(book, premium, today, "expired, intrinsic")
        else:
            premium = oc.mark_contract(pos, pcfg["option_spread_take"])
            if oc.near_expiry(pos, today_d, p["exit_dte"]) or sig["exit"]:
                reason = (f"<= {p['exit_dte']} DTE"
                          if oc.near_expiry(pos, today_d, p["exit_dte"])
                          else "exit signal")
                if premium is None:  # no quote: settle at intrinsic vs spot
                    premium = round(oc.intrinsic(pos, spot), 2)
                    reason += ", no chain quote, intrinsic"
                action = "CLOSE"
                detail = paper.close_option(book, premium, today, reason)
                premium = None
            else:
                action = "HOLD"
    elif sig["entry"]:
        contract = oc.pick_contract(spec["symbol"], spec["right"], spot, p,
                                    today_d, pcfg["option_spread_take"])
        if contract is None:
            detail = "entry signal but no contract in DTE window / no quote"
        else:
            filled = paper.open_option(book, contract, pcfg["option_alloc"], today)
            if filled is None:
                detail = (f"entry signal but 1 contract "
                          f"(~${contract['fill'] * 100:.0f}) exceeds book cash")
            else:
                action, detail = "OPEN", filled
                premium = contract["fill"]
    value = paper.mark(book, today, option_premium=premium)
    return {"action": action, "detail": detail, "reason": sig["reason"],
            "value": value}


def append_log(today: str, results: dict) -> None:
    lines = [f"\n## {today}\n", "| strategy | action | book value | detail |",
             "|---|---|---|---|"]
    for name, r in results.items():
        note = r["detail"] or r["reason"]
        lines.append(f"| {name} | {r['action']} | ${r['value']:,.2f} | {note} |")
    LOG_PATH.parent.mkdir(exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes", default="{}",
                    help='JSON object of live prices, e.g. {"SPY": 600.12}')
    ap.add_argument("--force", action="store_true",
                    help="allow a second run on the same date")
    ap.add_argument("--date", default=None, help="override today (tests only)")
    args = ap.parse_args()
    quotes = json.loads(args.quotes)
    today_d = date.fromisoformat(args.date) if args.date else date.today()
    today = str(today_d)

    enabled = {n: s for n, s in CONFIG["strategies"].items() if s.get("enabled")}
    pcfg = CONFIG["paper"]

    state = (json.loads(PAPER_PATH.read_text()) if PAPER_PATH.exists()
             else {"last_run_date": None, "books": {}})
    if state.get("last_run_date") == today and not args.force:
        print(json.dumps({"date": today, "skipped": True,
                          "reason": "already ran today (use --force to re-run)"}))
        return
    for name in enabled:
        state["books"].setdefault(name, paper.new_book(pcfg["starting_cash"], today))

    symbols = set()
    for spec in enabled.values():
        symbols.update(spec.get("symbols", [spec.get("symbol")]))
    dfs = {s: history_with_today(s, quotes, today_d) for s in sorted(symbols)}

    results = {}
    for name, spec in enabled.items():
        book = state["books"][name]
        try:
            if spec["kind"] == "equity":
                results[name] = run_equity(name, spec, book, dfs[spec["symbol"]],
                                           today, pcfg)
            elif spec["kind"] == "rotation":
                results[name] = run_rotation(
                    name, spec, book, {s: dfs[s] for s in spec["symbols"]},
                    today, pcfg)
            elif spec["kind"] == "option":
                results[name] = run_option(name, spec, book, dfs[spec["symbol"]],
                                           today_d, pcfg)
            else:
                results[name] = {"action": "ERROR", "detail": "",
                                 "reason": f"unknown kind {spec['kind']!r}",
                                 "value": paper.mark(book, today)}
        except Exception as exc:  # one bad strategy must not sink the fleet
            results[name] = {"action": "ERROR", "detail": "",
                             "reason": f"{type(exc).__name__}: {exc}",
                             "value": paper.mark(book, today)}

    state["last_run_date"] = today
    PAPER_PATH.parent.mkdir(exist_ok=True)
    tmp = PAPER_PATH.with_suffix(".json.tmp")  # atomic: a crash mid-write
    tmp.write_text(json.dumps(state, indent=2))  # must not truncate the books
    tmp.replace(PAPER_PATH)
    append_log(today, results)
    print(json.dumps({"date": today, "results": results}, indent=2))


if __name__ == "__main__":
    main()
