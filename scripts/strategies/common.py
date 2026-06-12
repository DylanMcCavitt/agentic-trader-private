"""Shared indicator math and data fetch for the strategy fleet.

rsi() must stay identical to scripts/decide.py and scripts/backtest.py.
"""
import pandas as pd
import yfinance as yf


def fetch_history(symbol: str, period: str = "2y") -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval="1d", auto_adjust=True, progress=False)
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
    return 100 - 100 / (1 + avg_gain / avg_loss)


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def ibs(df: pd.DataFrame) -> pd.Series:
    """Internal Bar Strength: (close - low) / (high - low); 0.5 on flat bars."""
    rng = df["High"] - df["Low"]
    return ((df["Close"] - df["Low"]) / rng).where(rng > 0, 0.5)
