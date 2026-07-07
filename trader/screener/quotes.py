"""Quote snapshots: fetch current quotes and persist them to the DB.

The gates use the latest ``quotes`` row per symbol for quote-freshness and
liquidity checks; the digest uses it to mark open positions to market.
Fetching is injectable so tests never touch the network.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from trader.screener import screens as screens_mod
from trader.screener import universe as universe_mod


@dataclass
class QuoteData:
    symbol: str
    price: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    avg_dollar_volume: float | None = None
    ts: datetime | None = None


QuoteFetchFn = Callable[[list[str]], list[QuoteData]]


def fetch_quotes(symbols: list[str]) -> list[QuoteData]:
    """Fetch current quotes via yfinance. Symbols that fail are skipped."""
    import yfinance as yf

    from trader.screener.run import fetch_bars

    bars_by_symbol = fetch_bars(symbols)
    out: list[QuoteData] = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            print(f"warning: no quote data for {symbol}", file=sys.stderr)
            continue
        metrics = screens_mod.compute_metrics(symbol, bars)
        if metrics is None:
            continue

        bid = ask = None
        try:
            fast = yf.Ticker(symbol).fast_info
            bid = getattr(fast, "bid", None) or None
            ask = getattr(fast, "ask", None) or None
        except Exception:
            pass  # bid/ask are best-effort; daily bars already give price

        out.append(
            QuoteData(
                symbol=symbol,
                price=metrics.last,
                bid=bid,
                ask=ask,
                volume=float(bars["Volume"].iloc[-1]),
                avg_dollar_volume=metrics.dollar_volume,
                ts=datetime.now(timezone.utc),
            )
        )
    return out


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(round(value, 4)))


def _default_account_id(session) -> int:
    from trader.db.models import Account

    try:  # prefer the gates' notion of "the" account when available
        from trader.gates import runtime

        account = runtime.get_account(session)
    except Exception:
        account = None
    if account is None:
        account = session.query(Account).order_by(Account.id).first()
    if account is None:
        account = Account(name="default")
        session.add(account)
        session.flush()
    return account.id


def snapshot(
    symbols: list[str],
    session,
    *,
    fetch: QuoteFetchFn = fetch_quotes,
    account_id: int | None = None,
) -> list:
    """Fetch quotes for ``symbols`` and write one ``quotes`` row each.

    Returns the persisted Quote rows. Symbols with no data are skipped
    (partial success); the caller decides whether zero rows is an error.
    Daily volume goes in ``payload`` (the quotes table stores dollar-volume
    natively; share volume is auxiliary).
    """
    from trader.db.models import Quote

    symbols = [universe_mod.normalize_symbol(s) for s in symbols]
    if account_id is None:
        account_id = _default_account_id(session)
    rows = []
    for q in fetch(symbols):
        row = Quote(
            account_id=account_id,
            symbol=q.symbol,
            kind="equity",
            quoted_at=q.ts or datetime.now(timezone.utc),
            price=_dec(q.price),
            bid=_dec(q.bid),
            ask=_dec(q.ask),
            avg_dollar_volume=_dec(q.avg_dollar_volume),
            payload=None if q.volume is None else {"volume": int(q.volume)},
        )
        session.add(row)
        rows.append(row)
    session.commit()
    return rows
