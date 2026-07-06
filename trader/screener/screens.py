"""Deterministic screen math over daily OHLCV bars.

All functions here are pure (no network): they take pandas DataFrames of
daily bars and produce per-symbol metrics, hard-floor verdicts, and screen
memberships. The yfinance fetch lives in :mod:`trader.screener.run` so this
layer is fully testable on synthetic data.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Any

import pandas as pd

# --- Hard floors (deterministic, per plan) ---------------------------------
MIN_AVG_DOLLAR_VOLUME = 50_000_000  # 20d average daily dollar volume
MIN_PRICE = 5.0

# --- Screen thresholds ------------------------------------------------------
MOVER_1D_PCT = 3.0        # 1-day move >= 3%
MOVER_5D_PCT = 8.0        # 5-day move >= 8%
VOLUME_SURGE_RATIO = 2.0  # today's volume >= 2x the 20d average
GAP_UP_PCT = 2.0          # open >= 2% above prior close

AVG_WINDOW = 20  # trading days for volume / dollar-volume averages


@dataclass
class SymbolMetrics:
    symbol: str
    last: float
    pct_chg_1d: float | None = None
    pct_chg_5d: float | None = None
    dollar_volume: float | None = None      # 20d avg daily dollar volume
    volume_ratio: float | None = None       # today's volume / 20d avg volume
    gap_pct: float | None = None            # today's open vs prior close
    screens: list[str] = field(default_factory=list)
    floors: dict[str, bool] = field(default_factory=dict)

    @property
    def passes_floors(self) -> bool:
        return bool(self.floors) and all(self.floors.values())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["passes_floors"] = self.passes_floors
        return d


def _clean(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    return None if math.isnan(value) else value


def compute_metrics(symbol: str, bars: pd.DataFrame) -> SymbolMetrics | None:
    """Compute screen metrics for one symbol from daily OHLCV bars.

    ``bars`` must have Open/High/Low/Close/Volume columns, oldest first.
    Returns None when there is not enough data for even a last price.
    """
    bars = bars.dropna(subset=["Close"])
    if bars.empty:
        return None

    close = bars["Close"]
    volume = bars["Volume"].fillna(0)
    last = float(close.iloc[-1])

    pct_chg_1d = None
    gap_pct = None
    if len(bars) >= 2:
        prev_close = float(close.iloc[-2])
        if prev_close > 0:
            pct_chg_1d = (last / prev_close - 1.0) * 100.0
            today_open = _clean(bars["Open"].iloc[-1])
            if today_open is not None and today_open > 0:
                gap_pct = (today_open / prev_close - 1.0) * 100.0

    pct_chg_5d = None
    if len(bars) >= 6:
        base = float(close.iloc[-6])
        if base > 0:
            pct_chg_5d = (last / base - 1.0) * 100.0

    # Averages over the window *before* today, so today's surge does not
    # inflate its own baseline.
    prior = bars.iloc[:-1].tail(AVG_WINDOW)
    dollar_volume = None
    volume_ratio = None
    if len(prior) > 0:
        avg_vol = float(prior["Volume"].fillna(0).mean())
        dollar_volume = float((prior["Close"] * prior["Volume"].fillna(0)).mean())
        today_vol = float(volume.iloc[-1])
        if avg_vol > 0:
            volume_ratio = today_vol / avg_vol

    return SymbolMetrics(
        symbol=symbol,
        last=last,
        pct_chg_1d=_clean(pct_chg_1d),
        pct_chg_5d=_clean(pct_chg_5d),
        dollar_volume=_clean(dollar_volume),
        volume_ratio=_clean(volume_ratio),
        gap_pct=_clean(gap_pct),
    )


def check_floors(m: SymbolMetrics) -> dict[str, bool]:
    """Hard floors: 20d avg dollar volume >= $50M, price >= $5.

    US listing is enforced by universe construction (index constituents and
    curated US-listed ETFs only), so it is not re-checked here.
    """
    return {
        "min_price": m.last >= MIN_PRICE,
        "min_avg_dollar_volume": (m.dollar_volume or 0.0) >= MIN_AVG_DOLLAR_VOLUME,
    }


def apply_screens(m: SymbolMetrics) -> list[str]:
    """Which momentum screens the symbol passes (independent of floors)."""
    screens = []
    if m.pct_chg_1d is not None and m.pct_chg_1d >= MOVER_1D_PCT:
        screens.append("movers_1d")
    if m.pct_chg_5d is not None and m.pct_chg_5d >= MOVER_5D_PCT:
        screens.append("movers_5d")
    if m.volume_ratio is not None and m.volume_ratio >= VOLUME_SURGE_RATIO:
        screens.append("volume_surge")
    if m.gap_pct is not None and m.gap_pct >= GAP_UP_PCT:
        screens.append("gap_up")
    return screens


def screen_symbols(bars_by_symbol: dict[str, pd.DataFrame]) -> list[SymbolMetrics]:
    """Full pipeline over pre-fetched bars: metrics -> floors -> screens.

    Returns only candidates that pass the hard floors AND at least one
    screen, sorted by 1-day % change descending (then symbol, for
    deterministic output).
    """
    candidates = []
    for symbol, bars in bars_by_symbol.items():
        metrics = compute_metrics(symbol, bars)
        if metrics is None:
            continue
        metrics.floors = check_floors(metrics)
        metrics.screens = apply_screens(metrics)
        if metrics.passes_floors and metrics.screens:
            candidates.append(metrics)
    candidates.sort(key=lambda m: (-(m.pct_chg_1d or float("-inf")), m.symbol))
    return candidates
