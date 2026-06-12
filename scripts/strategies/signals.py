"""Signal functions for the strategy fleet.

Contract: signal(df, p) -> dict with
  entry   bool — open (or add) exposure today
  exit    bool — close exposure today
  reason  str  — human-readable explanation of whichever side fired (or didn't)
  metrics dict — the indicator values behind the call, for the journal

df is a daily OHLC frame whose last row is today's provisional bar
(live/last price patched in as the close). Both entry and exit are always
computed; the engine picks the side that applies given the current position.
"""
import pandas as pd

from .common import ibs, rsi, sma


def rsi2_long(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    close = float(px.iloc[-1])
    trend = float(sma(px, p["sma_trend"]).iloc[-1])
    exit_ma = float(sma(px, p["sma_exit"]).iloc[-1])
    r2 = float(rsi(px, 2).iloc[-1])
    entry = close > trend and r2 < p["entry_rsi"]
    exit_ = close > exit_ma
    return {
        "entry": entry, "exit": exit_,
        "reason": (f"close {close:.2f} vs SMA{p['sma_trend']} {trend:.2f}, "
                   f"RSI2 {r2:.1f} (entry < {p['entry_rsi']}), "
                   f"exit SMA{p['sma_exit']} {exit_ma:.2f}"),
        "metrics": {"close": round(close, 2), "rsi2": round(r2, 2),
                    "sma_trend": round(trend, 2), "sma_exit": round(exit_ma, 2)},
    }


def rsi2_short(df: pd.DataFrame, p: dict) -> dict:
    """Bearish mean reversion: overbought rallies inside a downtrend."""
    px = df["Close"]
    close = float(px.iloc[-1])
    trend = float(sma(px, p["sma_trend"]).iloc[-1])
    r2 = float(rsi(px, 2).iloc[-1])
    entry = close < trend and r2 > p["entry_rsi"]
    exit_ = r2 < p["exit_rsi"]
    return {
        "entry": entry, "exit": exit_,
        "reason": (f"close {close:.2f} vs SMA{p['sma_trend']} {trend:.2f}, "
                   f"RSI2 {r2:.1f} (entry > {p['entry_rsi']}, exit < {p['exit_rsi']})"),
        "metrics": {"close": round(close, 2), "rsi2": round(r2, 2),
                    "sma_trend": round(trend, 2)},
    }


def ibs_long(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    close = float(px.iloc[-1])
    trend = float(sma(px, p["sma_trend"]).iloc[-1])
    exit_ma = float(sma(px, p["sma_exit"]).iloc[-1])
    bar = float(ibs(df).iloc[-1])
    entry = close > trend and bar < p["entry_ibs"]
    exit_ = bar > p["exit_ibs"] or close > exit_ma
    return {
        "entry": entry, "exit": exit_,
        "reason": (f"IBS {bar:.2f} (entry < {p['entry_ibs']}, exit > {p['exit_ibs']}), "
                   f"close {close:.2f} vs SMA{p['sma_trend']} {trend:.2f}"),
        "metrics": {"close": round(close, 2), "ibs": round(bar, 2),
                    "sma_trend": round(trend, 2), "sma_exit": round(exit_ma, 2)},
    }


def bollinger_long(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    close = float(px.iloc[-1])
    trend = float(sma(px, p["sma_trend"]).iloc[-1])
    mid = float(sma(px, p["bb_len"]).iloc[-1])
    sd = float(px.rolling(p["bb_len"]).std().iloc[-1])
    lower = mid - p["bb_std"] * sd
    entry = close > trend and close < lower
    exit_ = close >= mid
    return {
        "entry": entry, "exit": exit_,
        "reason": (f"close {close:.2f} vs lower band {lower:.2f} / mid {mid:.2f} "
                   f"(BB{p['bb_len']},{p['bb_std']}σ), SMA{p['sma_trend']} {trend:.2f}"),
        "metrics": {"close": round(close, 2), "bb_lower": round(lower, 2),
                    "bb_mid": round(mid, 2), "sma_trend": round(trend, 2)},
    }


def donchian_long(df: pd.DataFrame, p: dict) -> dict:
    """Breakout over the prior N-day high; exit under the prior M-day low.
    Channels exclude today's bar so the breakout compares against the past."""
    close = float(df["Close"].iloc[-1])
    hi = float(df["High"].iloc[:-1].rolling(p["entry_high"]).max().iloc[-1])
    lo = float(df["Low"].iloc[:-1].rolling(p["exit_low"]).min().iloc[-1])
    return {
        "entry": close > hi, "exit": close < lo,
        "reason": (f"close {close:.2f} vs {p['entry_high']}d high {hi:.2f} / "
                   f"{p['exit_low']}d low {lo:.2f}"),
        "metrics": {"close": round(close, 2), "channel_high": round(hi, 2),
                    "channel_low": round(lo, 2)},
    }


def donchian_short(df: pd.DataFrame, p: dict) -> dict:
    """Breakdown under the prior N-day low while below trend; exit over the
    prior M-day high."""
    px = df["Close"]
    close = float(px.iloc[-1])
    trend = float(sma(px, p["sma_trend"]).iloc[-1])
    lo = float(df["Low"].iloc[:-1].rolling(p["entry_low"]).min().iloc[-1])
    hi = float(df["High"].iloc[:-1].rolling(p["exit_high"]).max().iloc[-1])
    return {
        "entry": close < lo and close < trend, "exit": close > hi,
        "reason": (f"close {close:.2f} vs {p['entry_low']}d low {lo:.2f} / "
                   f"{p['exit_high']}d high {hi:.2f}, SMA{p['sma_trend']} {trend:.2f}"),
        "metrics": {"close": round(close, 2), "channel_low": round(lo, 2),
                    "channel_high": round(hi, 2), "sma_trend": round(trend, 2)},
    }


def momentum_rotation(dfs: dict, p: dict) -> dict:
    """Relative momentum across symbols: rank by lookback return, hold the
    leader while its return is positive, else cash. Not an entry/exit signal —
    returns the target symbol (or None for cash)."""
    rets = {}
    for symbol, df in dfs.items():
        px = df["Close"]
        if len(px) <= p["lookback"]:
            return {"target": None, "reason": f"only {len(px)} bars for {symbol}",
                    "metrics": {}}
        rets[symbol] = float(px.iloc[-1] / px.iloc[-(p["lookback"] + 1)] - 1)
    best = max(rets, key=rets.get)
    target = best if rets[best] > 0 else None
    detail = ", ".join(f"{s} {r:+.1%}" for s, r in sorted(rets.items()))
    return {
        "target": target,
        "reason": f"{p['lookback']}d returns: {detail} -> "
                  f"{'hold ' + best if target else 'cash (leader negative)'}",
        "metrics": {f"ret_{s}": round(r, 4) for s, r in rets.items()},
    }
