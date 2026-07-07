"""`trader reconcile` — match broker orders to gate-approved orders."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_reconcile(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.sleeves import reconcile as rec

    if args.file and args.file != "-":
        with open(args.file) as fh:
            raw = json.load(fh)
    else:
        raw = json.load(sys.stdin)

    broker_orders = rec.parse_broker_orders(raw)
    with get_session() as session:
        result = rec.reconcile(session, broker_orders)

    print(f"matched {result.matched} broker orders, wrote {result.fills_written} fills")
    if result.unauthorized:
        print(f"!! UNAUTHORIZED: {len(result.unauthorized)} broker order(s) with no gate ref_id:", file=sys.stderr)
        for order in result.unauthorized:
            print(f"!!   {json.dumps(order)[:300]}", file=sys.stderr)
    if result.missing_at_broker:
        print(
            f"!! MISSING AT BROKER: {len(result.missing_at_broker)} gate-approved order(s) "
            "with no broker record:",
            file=sys.stderr,
        )
        for ref_id in result.missing_at_broker:
            print(f"!!   ref_id={ref_id}", file=sys.stderr)
    if not result.clean:
        print("!! reconciliation FLAGGED — investigate before further trading", file=sys.stderr)
        return 1
    print("reconciliation clean")
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("reconcile", help="reconcile broker orders against gate ref_ids")
    p.add_argument(
        "--file",
        "-f",
        help="path to broker order-list JSON (default: stdin; '-' also means stdin)",
    )
    p.set_defaults(func=cmd_reconcile)
