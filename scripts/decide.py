# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "yfinance"]
# ///
"""Compute the RSI(2) mean-reversion decision for today.

Usage: uv run scripts/signal.py --price <live_price> --holding <true|false>

Fetches daily history (auto-adjusted) through the prior session, appends
--price as today's provisional close, and prints a decision JSON. Indicator
math must stay identical to scripts/backtest.py.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    return 100 - 100 / (1 + avg_gain / avg_loss)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--price", type=float, required=True, help="live price to use as today's provisional close")
    ap.add_argument("--holding", choices=["true", "false"], required=True)
    args = ap.parse_args()

    sym = CONFIG["symbol"]
    df = yf.download(sym, period="2y", interval="1d", auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    px = df["Close"].copy()
    px = px[px.index.date < date.today()]  # drop any partial bar for today
    if len(px) < CONFIG["sma_trend"] + 5:
        print(json.dumps({"decision": "ERROR", "reason": f"only {len(px)} bars of history"}))
        sys.exit(1)
    px.loc[pd.Timestamp(date.today())] = args.price

    sma_trend = px.rolling(CONFIG["sma_trend"]).mean().iloc[-1]
    sma_exit = px.rolling(CONFIG["sma_exit"]).mean().iloc[-1]
    rsi2 = rsi(px, 2).iloc[-1]
    close = px.iloc[-1]
    holding = args.holding == "true"

    if holding:
        decision = "SELL" if close > sma_exit else "HOLD"
        reason = (f"close {close:.2f} {'>' if decision == 'SELL' else '<='} "
                  f"SMA{CONFIG['sma_exit']} {sma_exit:.2f}")
    else:
        if close > sma_trend and rsi2 < CONFIG["entry_rsi"]:
            decision, reason = "BUY", (f"close {close:.2f} > SMA{CONFIG['sma_trend']} "
                                       f"{sma_trend:.2f} and RSI2 {rsi2:.1f} < {CONFIG['entry_rsi']}")
        else:
            decision = "NONE"
            reason = (f"close {close:.2f} vs SMA{CONFIG['sma_trend']} {sma_trend:.2f}, "
                      f"RSI2 {rsi2:.1f} (need < {CONFIG['entry_rsi']} and above trend)")

    print(json.dumps({
        "date": str(date.today()), "symbol": sym, "price": round(close, 2),
        "rsi2": round(float(rsi2), 2), "sma_trend": round(float(sma_trend), 2),
        "sma_exit": round(float(sma_exit), 2), "holding": holding,
        "decision": decision, "reason": reason,
    }))


if __name__ == "__main__":
    main()
