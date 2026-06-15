"""Paper book operations for the strategy fleet.

One book per strategy in state/paper.json: cash, at most one open position
(equity or option), closed-trade history, and a daily value series. Pure
functions over plain dicts — all I/O lives in run_strategies.py.
"""


DEFAULT_OPTION_FEE_PER_CONTRACT = 0.65


def new_book(starting_cash: float, today: str) -> dict:
    return {
        "cash": starting_cash, "starting_cash": starting_cash, "started": today,
        "position": None, "trades": [], "history": [],
    }


def open_equity(book: dict, symbol: str, price: float, slip_bps: float,
                fraction: float, today: str) -> str:
    fill = price * (1 + slip_bps / 1e4)
    spend = book["cash"] * fraction
    shares = spend / fill
    book["cash"] -= spend
    book["position"] = {"kind": "equity", "symbol": symbol, "shares": shares,
                        "entry_price": fill, "entry_date": today}
    return f"bought {shares:.4f} {symbol} @ {fill:.2f}"


def close_equity(book: dict, price: float, slip_bps: float, today: str,
                 reason: str) -> str:
    pos = book["position"]
    fill = price * (1 - slip_bps / 1e4)
    proceeds = pos["shares"] * fill
    cost = pos["shares"] * pos["entry_price"]
    book["cash"] += proceeds
    book["trades"].append({
        "opened": pos["entry_date"], "closed": today,
        "detail": f"{pos['symbol']} {pos['shares']:.4f} sh "
                  f"{pos['entry_price']:.2f} -> {fill:.2f} ({reason})",
        "pnl": round(proceeds - cost, 2),
        "ret": round(fill / pos["entry_price"] - 1, 6),
    })
    book["position"] = None
    return f"sold {pos['shares']:.4f} {pos['symbol']} @ {fill:.2f} ({reason})"


def open_option(book: dict, contract: dict, alloc: float, today: str,
                fee_per_contract: float = 0.0) -> str | None:
    """Buy floor(cash x alloc / premium+fee) contracts, at least 1 if affordable.
    Returns None (no fill) when even one contract exceeds the book's cash."""
    fee = float(fee_per_contract or 0.0)
    per_contract = contract["fill"] * 100 + fee
    if per_contract <= 0:
        return None
    n = int(book["cash"] * alloc / per_contract)
    if n < 1 and per_contract <= book["cash"]:
        n = 1
    if n < 1:
        return None
    book["cash"] -= n * per_contract
    pos = {"kind": "option", "contracts": n,
           "entry_premium": contract["fill"], "entry_date": today,
           "entry_fee_per_contract": fee,
           "underlying": contract["underlying"],
           "right": contract["right"],
           "strike": contract["strike"], "expiry": contract["expiry"]}
    for key in ("entry_iv", "entry_dte"):
        if key in contract:
            pos[key] = contract[key]
    book["position"] = pos
    return (f"bought {n}x {contract['underlying']} {contract['expiry']} "
            f"{contract['strike']:g}{contract['right'][0].upper()} @ {contract['fill']:.2f}")


def close_option(book: dict, premium: float, today: str, reason: str,
                 fee_per_contract: float = 0.0) -> str:
    pos = book["position"]
    contracts = pos["contracts"]
    close_fee = float(fee_per_contract or 0.0)
    gross_proceeds = contracts * premium * 100
    proceeds = gross_proceeds - contracts * close_fee
    cost = contracts * (pos["entry_premium"] * 100
                        + float(pos.get("entry_fee_per_contract", 0.0)))
    book["cash"] += proceeds
    book["trades"].append({
        "opened": pos["entry_date"], "closed": today,
        "detail": f"{contracts}x {pos['underlying']} {pos['expiry']} "
                  f"{pos['strike']:g}{pos['right'][0].upper()} "
                  f"{pos['entry_premium']:.2f} -> {premium:.2f} ({reason})",
        "pnl": round(proceeds - cost, 2),
        "ret": round(proceeds / cost - 1, 6) if cost else 0.0,
    })
    book["position"] = None
    return (f"sold {contracts}x {pos['underlying']} "
            f"{pos['strike']:g}{pos['right'][0].upper()} @ {premium:.2f} ({reason})")


def mark(book: dict, today: str, equity_price: float | None = None,
         option_premium: float | None = None) -> float:
    """Mark the book to market and record today's value (idempotent per day)."""
    value = book["cash"]
    pos = book["position"]
    if pos and pos["kind"] == "equity":
        value += pos["shares"] * (equity_price if equity_price is not None
                                  else pos["entry_price"])
    elif pos and pos["kind"] == "option":
        value += pos["contracts"] * 100 * (option_premium if option_premium is not None
                                           else pos["entry_premium"])
    value = round(value, 2)
    if book["history"] and book["history"][-1]["date"] == today:
        book["history"][-1]["value"] = value
    else:
        book["history"].append({"date": today, "value": value})
    return value


def stats(book: dict) -> dict:
    values = [h["value"] for h in book["history"]] or [book["starting_cash"]]
    peak, max_dd = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        max_dd = min(max_dd, v / peak - 1)
    trades = book["trades"]
    wins = sum(1 for t in trades if t["pnl"] > 0)
    return {
        "value": values[-1],
        "total_return": round(values[-1] / book["starting_cash"] - 1, 4),
        "max_drawdown": round(max_dd, 4),
        "trades": len(trades),
        "win_rate": round(wins / len(trades), 3) if trades else None,
        "days": len(book["history"]),
    }
