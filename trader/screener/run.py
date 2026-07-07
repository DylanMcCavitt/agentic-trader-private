"""Screener orchestration: fetch bars via yfinance, run the screens.

Network access is isolated here (and injectable) so the screen math in
:mod:`trader.screener.screens` stays testable offline.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any, Callable

import pandas as pd

from trader.screener import screens as screens_mod
from trader.screener import universe as universe_mod

BATCH_SIZE = 100
HISTORY_PERIOD = "3mo"  # enough for the 20d averages plus slack

FetchFn = Callable[[list[str]], dict[str, pd.DataFrame]]


def fetch_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """Batch-download daily bars for ``symbols`` via yf.download.

    Symbols that fail to download are simply absent from the result;
    yfinance logs its own warnings for those.
    """
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        try:
            data = yf.download(
                batch,
                period=HISTORY_PERIOD,
                interval="1d",
                group_by="ticker",
                auto_adjust=False,
                threads=True,
                progress=False,
            )
        except Exception as exc:  # a whole batch failing is survivable
            print(f"warning: batch download failed ({exc})", file=sys.stderr)
            continue
        if data is None or data.empty:
            continue
        for symbol in batch:
            try:
                bars = data[symbol] if isinstance(data.columns, pd.MultiIndex) else data
            except KeyError:
                continue
            bars = bars.dropna(subset=["Close"]) if "Close" in bars else pd.DataFrame()
            if not bars.empty:
                out[symbol] = bars
    return out


def run_screener(
    *,
    top: int | None = None,
    offline_universe: bool = False,
    fetch: FetchFn = fetch_bars,
) -> dict[str, Any]:
    """Build the universe, fetch bars, screen, and return a JSON-able report.

    Partial data failures degrade gracefully (candidates from whatever
    downloaded, with a warning); only a total fetch failure is fatal to the
    caller (empty ``candidates`` + ``fetched == 0``).
    """
    symbols, source = universe_mod.build_universe(offline=offline_universe)
    bars = fetch(symbols)

    warnings = []
    missing = len(symbols) - len(bars)
    if missing > 0:
        warnings.append(f"{missing}/{len(symbols)} symbols had no data")

    candidates = screens_mod.screen_symbols(bars)
    if top is not None:
        candidates = candidates[:top]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": {"size": len(symbols), "source": source},
        "fetched": len(bars),
        "floors": {
            "min_avg_dollar_volume": screens_mod.MIN_AVG_DOLLAR_VOLUME,
            "min_price": screens_mod.MIN_PRICE,
        },
        "warnings": warnings,
        "candidates": [m.to_dict() for m in candidates],
    }


def check_symbol(symbol: str, *, fetch: FetchFn = fetch_bars) -> dict[str, Any]:
    """Pass/fail verdict against the hard floors for one symbol."""
    symbol = universe_mod.normalize_symbol(symbol)
    bars = fetch([symbol]).get(symbol)
    if bars is None or bars.empty:
        return {"symbol": symbol, "ok": False, "error": "no data from yfinance"}
    metrics = screens_mod.compute_metrics(symbol, bars)
    if metrics is None:
        return {"symbol": symbol, "ok": False, "error": "insufficient bars"}
    metrics.floors = screens_mod.check_floors(metrics)
    metrics.screens = screens_mod.apply_screens(metrics)
    result = metrics.to_dict()
    result["ok"] = metrics.passes_floors
    return result
