# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas", "yfinance"]
# ///
"""Backtest Connors RSI(2) mean reversion, long-only, close-to-close fills.

Rules:
  entry: close > SMA200 and RSI(2) < entry_rsi  -> buy at close
  scale: optional second tranche when RSI(2) < scale_rsi
  exit:  close > SMA5                            -> sell at close
Costs: slippage_bps per side, zero commission.
"""
import pandas as pd
import yfinance as yf

SLIPPAGE_BPS = 2.0


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


def run(df: pd.DataFrame, entry_rsi=10.0, scale_rsi=5.0, start=None, end=None):
    px = df["Close"].copy()
    sma200 = px.rolling(200).mean()
    sma5 = px.rolling(5).mean()
    r2 = rsi(px, 2)

    if start:
        mask = px.index >= start
        px, sma200, sma5, r2 = px[mask], sma200[mask], sma5[mask], r2[mask]
    if end:
        mask = px.index <= end
        px, sma200, sma5, r2 = px[mask], sma200[mask], sma5[mask], r2[mask]

    slip = SLIPPAGE_BPS / 1e4
    cash, shares = 1.0, 0.0
    weight = 0.0  # 0, 0.5, 1.0
    equity = []
    trades = []
    entry_px = entry_date = None

    for i, (date, close) in enumerate(px.items()):
        if pd.notna(sma200.iloc[i]):
            in_pos = weight > 0
            if in_pos and close > sma5.iloc[i]:
                cash += shares * close * (1 - slip)
                trades.append({
                    "entry": entry_date, "exit": date,
                    "ret": close / entry_px - 1,
                    "days": (date - entry_date).days,
                })
                shares, weight = 0.0, 0.0
            elif close > sma200.iloc[i] and r2.iloc[i] < entry_rsi:
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
    for sym in ["SPY", "QQQ"]:
        df = fetch(sym)
        print(f"\n=== {sym} (data {df.index[0].date()} -> {df.index[-1].date()}) ===")
        for label, kw in [
            ("full  RSI<10 scale<5", {}),
            ("full  RSI<10 no-scale", {"scale_rsi": None}),
            ("2015+ RSI<10 scale<5", {"start": "2015-01-01"}),
            ("2020+ RSI<10 scale<5", {"start": "2020-01-01"}),
        ]:
            print(f"{label}: {run(df, **kw)}")
