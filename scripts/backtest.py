"""Backtest Connors RSI(2) mean reversion, long-only, close-to-close fills.

Rules:
  entry: close > SMA200 and RSI(2) < entry_rsi  -> buy at close
  scale: optional second tranche when RSI(2) < scale_rsi
  exit:  close > SMA5                            -> sell at close
Costs: slippage_bps per side, zero commission.

Defaults for entry_rsi, scale_rsi, and slippage_bps come from config.json
(the same config scripts/decide.py reads). CLI flags override them for
parameter sweeps only — they never change the live config.
"""
import argparse
import json
from pathlib import Path

import pandas as pd
import yfinance as yf

CONFIG = json.loads((Path(__file__).parent.parent / "config.json").read_text())

_UNSET = object()  # sentinel: scale_rsi=None is meaningful (no scale-in)


def fetch(symbol: str) -> pd.DataFrame:
    df = yf.download(symbol, period="max", interval="1d", auto_adjust=True, progress=False)
    if df.empty or "Close" not in df:
        raise RuntimeError(f"no data for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def run(df: pd.DataFrame, entry_rsi=None, scale_rsi=_UNSET, start=None, end=None,
        stop_pct=None, slippage_bps=None):
    """stop_pct: intraday stop-loss below entry (e.g. 0.08). Fills at the open
    when the day gaps through the stop, else at the stop price. No same-day
    re-entry after a stop-out.

    entry_rsi/scale_rsi/slippage_bps default to config.json values when not
    given; pass scale_rsi=None to disable the second tranche."""
    if entry_rsi is None:
        entry_rsi = CONFIG["entry_rsi"]
    if scale_rsi is _UNSET:
        scale_rsi = CONFIG["scale_rsi"]
    if slippage_bps is None:
        slippage_bps = CONFIG["slippage_bps"]
    px = df["Close"].copy()
    opens = df["Open"].copy()
    lows = df["Low"].copy()
    sma200 = px.rolling(200).mean()
    sma5 = px.rolling(5).mean()
    r2 = rsi(px, 2)

    if start:
        mask = px.index >= start
        px, sma200, sma5, r2 = px[mask], sma200[mask], sma5[mask], r2[mask]
        opens, lows = opens[mask], lows[mask]
    if end:
        mask = px.index <= end
        px, sma200, sma5, r2 = px[mask], sma200[mask], sma5[mask], r2[mask]
        opens, lows = opens[mask], lows[mask]

    slip = slippage_bps / 1e4
    cash, shares = 1.0, 0.0
    weight = 0.0  # 0, 0.5, 1.0
    equity = []
    trades = []
    entry_px = entry_date = None

    for i, (date, close) in enumerate(px.items()):
        if pd.notna(sma200.iloc[i]):
            in_pos = weight > 0
            stopped_today = False
            if in_pos and stop_pct and entry_date is not None and date > entry_date:
                stop = entry_px * (1 - stop_pct)
                fill = opens.iloc[i] if opens.iloc[i] <= stop else (
                    stop if lows.iloc[i] <= stop else None)
                if fill is not None:
                    cash += shares * fill * (1 - slip)
                    trades.append({
                        "entry": entry_date, "exit": date,
                        "ret": fill / entry_px - 1,
                        "days": (date - entry_date).days,
                    })
                    shares, weight = 0.0, 0.0
                    in_pos, stopped_today = False, True
            if in_pos and close > sma5.iloc[i]:
                cash += shares * close * (1 - slip)
                trades.append({
                    "entry": entry_date, "exit": date,
                    "ret": close / entry_px - 1,
                    "days": (date - entry_date).days,
                })
                shares, weight = 0.0, 0.0
            elif not stopped_today and close > sma200.iloc[i] and r2.iloc[i] < entry_rsi:
                target = 1.0 if (scale_rsi and r2.iloc[i] < scale_rsi) else 0.5
                if scale_rsi is None:
                    target = 1.0
                if target > weight:
                    eq = cash + shares * close
                    add = eq * (target - weight)
                    if not in_pos:
                        entry_px, entry_date = close, date
                    shares += add / (close * (1 + slip))
                    cash -= add
                    weight = target
        equity.append(cash + shares * close)

    eq = pd.Series(equity, index=px.index)
    rets = eq.pct_change().dropna()
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = eq.iloc[-1] ** (1 / years) - 1
    dd = (eq / eq.cummax() - 1).min()
    sharpe = rets.mean() / rets.std() * (252 ** 0.5) if rets.std() > 0 else 0
    tdf = pd.DataFrame(trades)
    bh = px.iloc[-1] / px.iloc[0]
    bh_cagr = bh ** (1 / years) - 1
    return {
        "years": round(years, 1), "cagr": f"{cagr:.2%}", "bh_cagr": f"{bh_cagr:.2%}",
        "sharpe": round(sharpe, 2), "max_dd": f"{dd:.2%}",
        "trades": len(tdf), "trades_yr": round(len(tdf) / years, 1),
        "win_rate": f"{(tdf.ret > 0).mean():.1%}" if len(tdf) else "n/a",
        "avg_hold_d": round(tdf.days.mean(), 1) if len(tdf) else 0,
        "avg_ret": f"{tdf.ret.mean():.2%}" if len(tdf) else "n/a",
        "worst_trade": f"{tdf.ret.min():.2%}" if len(tdf) else "n/a",
        "exposure": f"{(eq.pct_change() != 0).mean():.0%}",
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--entry-rsi", type=float, default=None,
                    help=f"override config entry_rsi (config: {CONFIG['entry_rsi']})")
    ap.add_argument("--scale-rsi", type=float, default=None,
                    help=f"override config scale_rsi (config: {CONFIG['scale_rsi']})")
    ap.add_argument("--slippage-bps", type=float, default=None,
                    help=f"override config slippage_bps (config: {CONFIG['slippage_bps']})")
    args = ap.parse_args()
    base = {k: v for k, v in {
        "entry_rsi": args.entry_rsi, "scale_rsi": args.scale_rsi,
        "slippage_bps": args.slippage_bps,
    }.items() if v is not None}
    er = base.get("entry_rsi", CONFIG["entry_rsi"])
    sr = base.get("scale_rsi", CONFIG["scale_rsi"])

    for sym in ["SPY", "QQQ"]:
        df = fetch(sym)
        print(f"\n=== {sym} (data {df.index[0].date()} -> {df.index[-1].date()}) ===")
        for label, kw in [
            (f"full  RSI<{er:g} scale<{sr:g}", {}),
            (f"full  RSI<{er:g} no-scale", {"scale_rsi": None}),
            (f"2015+ RSI<{er:g} scale<{sr:g}", {"start": "2015-01-01"}),
            (f"2020+ RSI<{er:g} scale<{sr:g}", {"start": "2020-01-01"}),
            ("full  no-scale stop8%", {"scale_rsi": None, "stop_pct": 0.08}),
            ("full  no-scale stop5%", {"scale_rsi": None, "stop_pct": 0.05}),
            ("full  no-scale stop3%", {"scale_rsi": None, "stop_pct": 0.03}),
            ("2015+ no-scale stop5%", {"scale_rsi": None, "stop_pct": 0.05, "start": "2015-01-01"}),
        ]:
            print(f"{label}: {run(df, **{**base, **kw})}")
