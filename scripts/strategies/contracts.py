"""Option contract selection and marking via yfinance chains.

Paper-trading only: quotes are ~15-minute delayed, which is acceptable for
comparing strategies. Live options orders (if ever promoted) go through the
Robinhood MCP and scripts/option_gate.py instead.

Fill model: buys pay mid + spread_take x (ask - mid); sells receive
mid - spread_take x (mid - bid). Mid falls back to lastPrice when the
bid/ask is missing or crossed.
"""
from datetime import date, timedelta

import yfinance as yf


def _mid(row) -> float | None:
    bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
    if bid > 0 and ask >= bid:
        return (bid + ask) / 2
    last = float(row.get("lastPrice") or 0)
    return last if last > 0 else None


def _fill(row, side: str, spread_take: float) -> float | None:
    mid = _mid(row)
    if mid is None:
        return None
    bid, ask = float(row.get("bid") or 0), float(row.get("ask") or 0)
    if bid > 0 and ask >= bid:
        half = (ask - bid) / 2
        return mid + spread_take * half if side == "buy" else mid - spread_take * half
    return mid


def pick_contract(symbol: str, right: str, spot: float, p: dict, today: date,
                  spread_take: float) -> dict | None:
    """Nearest expiry inside [dte_min, dte_max], strike nearest itm_pct in the
    money. Returns the contract plus a buy-side fill price, or None."""
    tkr = yf.Ticker(symbol)
    expiries = []
    for d in tkr.options or ():
        dte = (date.fromisoformat(d) - today).days
        if p["dte_min"] <= dte <= p["dte_max"]:
            expiries.append(d)
    if not expiries:
        return None
    expiry = min(expiries)
    chain = tkr.option_chain(expiry)
    table = chain.calls if right == "call" else chain.puts
    if table.empty:
        return None
    target = spot * (1 - p["itm_pct"]) if right == "call" else spot * (1 + p["itm_pct"])
    row = table.loc[(table["strike"] - target).abs().idxmin()]
    fill = _fill(row, "buy", spread_take)
    if fill is None or fill <= 0:
        return None
    return {
        "underlying": symbol, "right": right,
        "strike": float(row["strike"]), "expiry": expiry,
        "fill": round(fill, 2),
    }


def mark_contract(pos: dict, spread_take: float) -> float | None:
    """Sell-side value of an open position's contract, or None if the chain
    row can't be found (e.g. just after expiry — caller settles at intrinsic)."""
    tkr = yf.Ticker(pos["underlying"])
    if pos["expiry"] not in (tkr.options or ()):
        return None
    chain = tkr.option_chain(pos["expiry"])
    table = chain.calls if pos["right"] == "call" else chain.puts
    rows = table[table["strike"] == pos["strike"]]
    if rows.empty:
        return None
    return _fill(rows.iloc[0], "sell", spread_take)


def intrinsic(pos: dict, underlying_close: float) -> float:
    if pos["right"] == "call":
        return max(0.0, underlying_close - pos["strike"])
    return max(0.0, pos["strike"] - underlying_close)


def is_expired(pos: dict, today: date) -> bool:
    return today > date.fromisoformat(pos["expiry"])


def near_expiry(pos: dict, today: date, exit_dte: int) -> bool:
    return (date.fromisoformat(pos["expiry"]) - today) <= timedelta(days=exit_dte)
