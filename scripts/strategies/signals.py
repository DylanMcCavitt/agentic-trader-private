"""Signal functions for the strategy fleet.

Two layers, one source of truth:

- *_series(df, p) -> {"entry": bool Series, "exit": bool Series, "ind": {...}}
  vectorized over the whole frame — used by scripts/backtest_fleet.py.
- the scalar functions (registered in SIGNALS) take the last row of the
  series version and add a human-readable reason — used by the live paper
  engine (scripts/run_strategies.py).

df is a daily OHLC frame; for the live path its last row is today's
provisional bar (live/last price patched in as the close). NaN indicator
values (warm-up window) compare False, so neither side fires.
"""
import pandas as pd

from .common import ibs, rsi, sma


def _last(series_out: dict) -> dict:
    return {
        "entry": bool(series_out["entry"].iloc[-1]),
        "exit": bool(series_out["exit"].iloc[-1]),
        "metrics": {k: round(float(v.iloc[-1]), 2) for k, v in series_out["ind"].items()},
    }


def rsi2_long_series(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    trend = sma(px, p["sma_trend"])
    exit_ma = sma(px, p["sma_exit"])
    r2 = rsi(px, 2)
    return {"entry": (px > trend) & (r2 < p["entry_rsi"]),
            "exit": px > exit_ma,
            "ind": {"close": px, "rsi2": r2, "sma_trend": trend, "sma_exit": exit_ma}}


def rsi2_long(df: pd.DataFrame, p: dict) -> dict:
    out = _last(rsi2_long_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"close {m['close']:.2f} vs SMA{p['sma_trend']} {m['sma_trend']:.2f}, "
                     f"RSI2 {m['rsi2']:.1f} (entry < {p['entry_rsi']}), "
                     f"exit SMA{p['sma_exit']} {m['sma_exit']:.2f}")
    return out


def rsi2_short_series(df: pd.DataFrame, p: dict) -> dict:
    """Bearish mean reversion: overbought rallies inside a downtrend."""
    px = df["Close"]
    trend = sma(px, p["sma_trend"])
    r2 = rsi(px, 2)
    return {"entry": (px < trend) & (r2 > p["entry_rsi"]),
            "exit": r2 < p["exit_rsi"],
            "ind": {"close": px, "rsi2": r2, "sma_trend": trend}}


def rsi2_short(df: pd.DataFrame, p: dict) -> dict:
    out = _last(rsi2_short_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"close {m['close']:.2f} vs SMA{p['sma_trend']} {m['sma_trend']:.2f}, "
                     f"RSI2 {m['rsi2']:.1f} (entry > {p['entry_rsi']}, "
                     f"exit < {p['exit_rsi']})")
    return out


def ibs_long_series(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    trend = sma(px, p["sma_trend"])
    exit_ma = sma(px, p["sma_exit"])
    bar = ibs(df)
    return {"entry": (px > trend) & (bar < p["entry_ibs"]),
            "exit": (bar > p["exit_ibs"]) | (px > exit_ma),
            "ind": {"close": px, "ibs": bar, "sma_trend": trend, "sma_exit": exit_ma}}


def ibs_long(df: pd.DataFrame, p: dict) -> dict:
    out = _last(ibs_long_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"IBS {m['ibs']:.2f} (entry < {p['entry_ibs']}, "
                     f"exit > {p['exit_ibs']}), close {m['close']:.2f} vs "
                     f"SMA{p['sma_trend']} {m['sma_trend']:.2f}")
    return out


def bollinger_long_series(df: pd.DataFrame, p: dict) -> dict:
    px = df["Close"]
    trend = sma(px, p["sma_trend"])
    mid = sma(px, p["bb_len"])
    lower = mid - p["bb_std"] * px.rolling(p["bb_len"]).std()
    return {"entry": (px > trend) & (px < lower),
            "exit": px >= mid,
            "ind": {"close": px, "bb_lower": lower, "bb_mid": mid, "sma_trend": trend}}


def bollinger_long(df: pd.DataFrame, p: dict) -> dict:
    out = _last(bollinger_long_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"close {m['close']:.2f} vs lower band {m['bb_lower']:.2f} / "
                     f"mid {m['bb_mid']:.2f} (BB{p['bb_len']},{p['bb_std']}σ), "
                     f"SMA{p['sma_trend']} {m['sma_trend']:.2f}")
    return out


def donchian_long_series(df: pd.DataFrame, p: dict) -> dict:
    """Breakout over the prior N-day high; exit under the prior M-day low.
    shift(1) keeps today's bar out of its own channel."""
    px = df["Close"]
    hi = df["High"].shift(1).rolling(p["entry_high"]).max()
    lo = df["Low"].shift(1).rolling(p["exit_low"]).min()
    return {"entry": px > hi, "exit": px < lo,
            "ind": {"close": px, "channel_high": hi, "channel_low": lo}}


def donchian_long(df: pd.DataFrame, p: dict) -> dict:
    out = _last(donchian_long_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"close {m['close']:.2f} vs {p['entry_high']}d high "
                     f"{m['channel_high']:.2f} / {p['exit_low']}d low "
                     f"{m['channel_low']:.2f}")
    return out


def donchian_short_series(df: pd.DataFrame, p: dict) -> dict:
    """Breakdown under the prior N-day low while below trend; exit over the
    prior M-day high."""
    px = df["Close"]
    trend = sma(px, p["sma_trend"])
    lo = df["Low"].shift(1).rolling(p["entry_low"]).min()
    hi = df["High"].shift(1).rolling(p["exit_high"]).max()
    return {"entry": (px < lo) & (px < trend), "exit": px > hi,
            "ind": {"close": px, "channel_low": lo, "channel_high": hi,
                    "sma_trend": trend}}


def donchian_short(df: pd.DataFrame, p: dict) -> dict:
    out = _last(donchian_short_series(df, p))
    m = out["metrics"]
    out["reason"] = (f"close {m['close']:.2f} vs {p['entry_low']}d low "
                     f"{m['channel_low']:.2f} / {p['exit_high']}d high "
                     f"{m['channel_high']:.2f}, SMA{p['sma_trend']} {m['sma_trend']:.2f}")
    return out


def rotation_targets(dfs: dict, p: dict) -> pd.Series:
    """Per-day rotation target over the symbols' common dates: the leader by
    lookback return while positive, else None (cash)."""
    closes = pd.DataFrame({s: df["Close"] for s, df in dfs.items()}).dropna()
    rets = (closes / closes.shift(p["lookback"]) - 1).dropna()
    if rets.empty:
        return pd.Series(dtype=object)
    leader = rets.idxmax(axis=1).astype(object)
    leader[~(rets.max(axis=1) > 0)] = None
    return leader


def momentum_rotation(dfs: dict, p: dict) -> dict:
    """Scalar wrapper for the live engine: today's target plus the reason."""
    for symbol, df in dfs.items():
        if len(df) <= p["lookback"]:
            return {"target": None, "reason": f"only {len(df)} bars for {symbol}",
                    "metrics": {}}
    rets = {s: float(df["Close"].iloc[-1] / df["Close"].iloc[-(p["lookback"] + 1)] - 1)
            for s, df in dfs.items()}
    best = max(rets, key=rets.get)
    target = best if rets[best] > 0 else None
    detail = ", ".join(f"{s} {r:+.1%}" for s, r in sorted(rets.items()))
    return {
        "target": target,
        "reason": f"{p['lookback']}d returns: {detail} -> "
                  f"{'hold ' + best if target else 'cash (leader negative)'}",
        "metrics": {f"ret_{s}": round(r, 4) for s, r in rets.items()},
    }
