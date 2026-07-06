"""`trader quotes record` — store quote/screen snapshots for the gates.

The execution lane records a fresh quote for each symbol/contract right
before composing an order; the gates enforce that a quote exists and is
recent. Input is a JSON object or list of objects on stdin (or --file):

  {"symbol": "NVDA", "kind": "equity", "price": 190.5,
   "avg_dollar_volume": 3.2e10}
  {"symbol": "NVDA", "kind": "option", "occ_symbol": "NVDA260821C00200000",
   "bid": 4.9, "ask": 5.1, "open_interest": 1200}
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal


def _dec(value):
    return Decimal(str(value)) if value is not None else None


def cmd_record(args: argparse.Namespace) -> int:
    from trader.db.models import Quote
    from trader.db.session import get_session
    from trader.gates import runtime

    if args.file and args.file != "-":
        with open(args.file) as fh:
            raw = json.load(fh)
    else:
        raw = json.load(sys.stdin)
    items = raw if isinstance(raw, list) else [raw]

    with get_session() as session:
        account = runtime.get_account(session)
        if account is None:
            print("no account row — run `trader sleeves init` first", file=sys.stderr)
            return 1
        count = 0
        for item in items:
            if not isinstance(item, dict) or not item.get("symbol"):
                print(f"skipping malformed quote item: {item!r}", file=sys.stderr)
                continue
            session.add(
                Quote(
                    account_id=account.id,
                    symbol=str(item["symbol"]).upper(),
                    kind=str(item.get("kind", "equity")),
                    price=_dec(item.get("price")),
                    bid=_dec(item.get("bid")),
                    ask=_dec(item.get("ask")),
                    avg_dollar_volume=_dec(item.get("avg_dollar_volume")),
                    open_interest=_dec(item.get("open_interest")),
                    occ_symbol=item.get("occ_symbol"),
                    payload=item,
                )
            )
            count += 1
        session.commit()
    print(f"recorded {count} quote(s)")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """`trader quotes snapshot SYMBOL...` — fetch via yfinance and store."""
    from trader.db.session import get_session
    from trader.screener.quotes import snapshot

    with get_session() as session:
        rows = snapshot(args.symbols, session)
        written = [
            {
                "symbol": r.symbol,
                "quoted_at": r.quoted_at.isoformat(),
                "price": None if r.price is None else float(r.price),
                "bid": None if r.bid is None else float(r.bid),
                "ask": None if r.ask is None else float(r.ask),
                "volume": (r.payload or {}).get("volume"),
                "avg_dollar_volume": None
                if r.avg_dollar_volume is None
                else float(r.avg_dollar_volume),
            }
            for r in rows
        ]
    print(json.dumps({"written": written}, indent=2))

    if not rows:
        print("error: no quotes could be fetched", file=sys.stderr)
        return 1
    if len(rows) < len(args.symbols):
        print(f"warning: wrote {len(rows)}/{len(args.symbols)} quotes", file=sys.stderr)
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("quotes", help="quote snapshots for gate checks")
    sub = p.add_subparsers(dest="quotes_command", required=True)

    record = sub.add_parser("record", help="record quote snapshot(s) from JSON")
    record.add_argument("--file", "-f", help="path to quote JSON (default: stdin)")
    record.set_defaults(func=cmd_record)

    snap = sub.add_parser("snapshot", help="fetch quotes via yfinance and store them")
    snap.add_argument("symbols", nargs="+", metavar="SYMBOL")
    snap.set_defaults(func=cmd_snapshot)
