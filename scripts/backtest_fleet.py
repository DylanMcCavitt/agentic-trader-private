"""Backtest the whole strategy fleet on daily history.

Usage: uv run scripts/backtest_fleet.py [--start 2005-01-01] [--end ...]
                                        [--iv-premium 1.15] [--opt-slip-pct 0.015]
                                        [--rate 0.04] [--json]

Replays the exact vectorized signals the paper fleet trades
(scripts/strategies/signals.py) through the same fill math
(scripts/paper.py), one $10k book per strategy, close-to-close fills.

Options strategies are an APPROXIMATION: there is no free historical option
chain, so contracts are synthesized at the configured moneyness/DTE and
priced with Black-Scholes using 21d EWMA realized vol x --iv-premium, with
--opt-slip-pct charged per side. That misses real IV dynamics (especially
vol crush after mean-reversion entries) — treat options rows as directional
feel, not truth. For chain-accurate options backtests use QuantConnect/LEAN.

Indicators warm up on data before --start; trading begins at --start.
"""
import argparse
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import paper
from strategies import SIGNAL_SERIES, signals
from strategies.common import fetch_history
from strategies.contracts import intrinsic
from strategies.pricing import bs_price

ROOT = Path(__file__).parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())


def realized_vol(px: pd.Series, span: int = 21) -> pd.Series:
    rets = np.log(px / px.shift(1))
    return rets.ewm(span=span).std() * math.sqrt(252)


def perf(book: dict) -> dict:
    hist = book["history"]
    eq = pd.Series([h["value"] for h in hist],
                   index=pd.to_datetime([h["date"] for h in hist]))
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    rets = eq.pct_change().dropna()
    trades = book["trades"]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "years": round(years, 1),
        "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1,
        "sharpe": (rets.mean() / rets.std() * math.sqrt(252)
                   if len(rets) and rets.std() > 0 else 0.0),
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "trades_yr": len(trades) / years,
        "win_rate": wins / len(trades) if trades else None,
        "exposure": float((rets != 0).mean()) if len(rets) else 0.0,
        "final": float(eq.iloc[-1]),
    }


def backtest_equity(spec: dict, df: pd.DataFrame, pcfg: dict,
                    start: pd.Timestamp) -> dict:
    sig = SIGNAL_SERIES[spec["signal"]](df, spec["params"])
    book = None
    for i, ts in enumerate(df.index):
        if ts < start:
            continue
        d = str(ts.date())
        if book is None:
            book = paper.new_book(pcfg["starting_cash"], d)
        px = float(df["Close"].iloc[i])
        if book["position"]:
            if bool(sig["exit"].iloc[i]):
                paper.close_equity(book, px, pcfg["slippage_bps"], d, "exit signal")
        elif bool(sig["entry"].iloc[i]):
            paper.open_equity(book, spec["symbol"], px, pcfg["slippage_bps"],
                              pcfg["position_fraction"], d)
        paper.mark(book, d, equity_price=px)
    return book


def backtest_rotation(spec: dict, dfs: dict, pcfg: dict,
                      start: pd.Timestamp) -> dict:
    targets = signals.rotation_targets(dfs, spec["params"])
    closes = pd.DataFrame({s: df["Close"] for s, df in dfs.items()}).dropna()
    book = None
    for ts, target in targets.items():
        if ts < start:
            continue
        d = str(ts.date())
        if book is None:
            book = paper.new_book(pcfg["starting_cash"], d)
        prices = closes.loc[ts]
        held = book["position"]["symbol"] if book["position"] else None
        if held and held != target:
            paper.close_equity(book, float(prices[held]), pcfg["slippage_bps"],
                               d, "rotation" if target else "risk-off")
        if target and book["position"] is None:
            paper.open_equity(book, target, float(prices[target]),
                              pcfg["slippage_bps"], pcfg["position_fraction"], d)
        held = book["position"]["symbol"] if book["position"] else None
        paper.mark(book, d,
                   equity_price=float(prices[held]) if held else None)
    return book


def backtest_option(spec: dict, df: pd.DataFrame, pcfg: dict,
                    start: pd.Timestamp, iv_premium: float, opt_slip: float,
                    r: float) -> dict:
    p = spec["params"]
    right = spec["right"]
    sig = SIGNAL_SERIES[spec["signal"]](df, p)
    vol = realized_vol(df["Close"]) * iv_premium
    book = None
    for i, ts in enumerate(df.index):
        if ts < start:
            continue
        d = ts.date()
        ds = str(d)
        if book is None:
            book = paper.new_book(pcfg["starting_cash"], ds)
        S = float(df["Close"].iloc[i])
        sg = float(vol.iloc[i]) if pd.notna(vol.iloc[i]) else 0.0
        pos = book["position"]
        premium_mark = None
        if pos:
            expiry = date.fromisoformat(pos["expiry"])
            if d >= expiry:
                paper.close_option(book, round(intrinsic(pos, S), 2), ds,
                                   "expired, intrinsic")
            else:
                t_years = (expiry - d).days / 365.25
                prem = bs_price(S, pos["strike"], t_years, sg, r, right)
                if (expiry - d).days <= p["exit_dte"] or bool(sig["exit"].iloc[i]):
                    reason = ("dte stop" if (expiry - d).days <= p["exit_dte"]
                              else "exit signal")
                    paper.close_option(book, round(prem * (1 - opt_slip), 2),
                                       ds, reason)
                else:
                    premium_mark = prem
        elif bool(sig["entry"].iloc[i]) and sg > 0:
            strike = float(round(S * (1 - p["itm_pct"]) if right == "call"
                                 else S * (1 + p["itm_pct"])))
            dte = (p["dte_min"] + p["dte_max"]) // 2
            t_years = dte / 365.25
            fill = bs_price(S, strike, t_years, sg, r, right) * (1 + opt_slip)
            if fill > 0.05:
                contract = {"underlying": spec["symbol"], "right": right,
                            "strike": strike, "expiry": str(d + timedelta(days=dte)),
                            "fill": round(fill, 2)}
                if paper.open_option(book, contract, pcfg["option_alloc"], ds):
                    premium_mark = contract["fill"]
        paper.mark(book, ds, option_premium=premium_mark)
    return book


def buy_and_hold(symbol: str, df: pd.DataFrame, pcfg: dict,
                 start: pd.Timestamp) -> dict:
    df = df[df.index >= start]
    book = paper.new_book(pcfg["starting_cash"], str(df.index[0].date()))
    paper.open_equity(book, symbol, float(df["Close"].iloc[0]),
                      pcfg["slippage_bps"], 1.0, str(df.index[0].date()))
    for ts, px in df["Close"].items():
        paper.mark(book, str(ts.date()), equity_price=float(px))
    return book


def build_fleet_books(start: pd.Timestamp, end: str | None = None,
                      iv_premium: float = 1.15, opt_slip: float = 0.015,
                      rate: float = 0.04) -> dict:
    """Backtest every enabled strategy plus hold_<symbol> buy-and-hold
    baselines; returns {name: book} with each book's daily value history.
    Additive entry point reused by scripts/replay_allocator.py; the CLI
    below renders its perf rows from exactly these books."""
    pcfg = CONFIG["paper"]
    enabled = {n: s for n, s in CONFIG["strategies"].items() if s.get("enabled")}
    symbols = set()
    for spec in enabled.values():
        symbols.update(spec.get("symbols", [spec.get("symbol")]))
    dfs = {}
    for s in sorted(symbols):
        df = fetch_history(s, period="max")
        if end:
            df = df[df.index <= pd.Timestamp(end)]
        dfs[s] = df

    books = {}
    for name, spec in enabled.items():
        if spec["kind"] == "equity":
            book = backtest_equity(spec, dfs[spec["symbol"]], pcfg, start)
        elif spec["kind"] == "rotation":
            book = backtest_rotation(spec, {s: dfs[s] for s in spec["symbols"]},
                                     pcfg, start)
        elif spec["kind"] == "option":
            book = backtest_option(spec, dfs[spec["symbol"]], pcfg, start,
                                   iv_premium, opt_slip, rate)
        else:
            continue
        books[name] = book
    for s in sorted(symbols):
        books[f"hold_{s.lower()}"] = buy_and_hold(s, dfs[s], pcfg, start)
    return books


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--iv-premium", type=float, default=1.15,
                    help="multiplier on realized vol to approximate IV")
    ap.add_argument("--opt-slip-pct", type=float, default=0.015,
                    help="per-side haircut on option fills")
    ap.add_argument("--rate", type=float, default=0.04, help="risk-free rate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    start = pd.Timestamp(args.start)

    books = build_fleet_books(start, args.end, args.iv_premium,
                              args.opt_slip_pct, args.rate)
    rows = {name: perf(book) for name, book in books.items()}

    if args.json:
        print(json.dumps(rows, indent=2, default=float))
        return
    print(f"fleet backtest {args.start} -> {args.end or 'today'} "
          f"(options approximated via Black-Scholes, iv_premium "
          f"{args.iv_premium}, opt slip {args.opt_slip_pct:.1%}/side)\n")
    header = (f"{'strategy':<24} {'years':>5} {'CAGR':>8} {'sharpe':>6} "
              f"{'maxDD':>8} {'tr/yr':>5} {'win%':>5} {'expo':>5} {'final':>12}")
    print(header)
    print("-" * len(header))
    for name, r in sorted(rows.items(), key=lambda kv: -kv[1]["cagr"]):
        win = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "-"
        print(f"{name:<24} {r['years']:>5} {r['cagr']:>8.2%} {r['sharpe']:>6.2f} "
              f"{r['max_dd']:>8.2%} {r['trades_yr']:>5.1f} {win:>5} "
              f"{r['exposure']:>5.0%} {r['final']:>12,.0f}")


if __name__ == "__main__":
    main()
