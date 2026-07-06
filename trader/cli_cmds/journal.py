"""`trader journal ...` — git-tracked markdown mirror of DB events."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime


def _parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def cmd_write(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.journal.writer import upsert_day

    day = _parse_date(args.date)
    try:
        with get_session() as session:
            path = upsert_day(session, day)
    except Exception as exc:
        print(f"error: could not write journal: {exc}", file=sys.stderr)
        return 1
    print(f"written: {path}")
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("journal", help="git-tracked daily journal")
    sub = p.add_subparsers(dest="journal_command", required=True)

    write = sub.add_parser("write", help="write/replace one day's journal section")
    write.add_argument("--date", help="YYYY-MM-DD (default: today)")
    write.set_defaults(func=cmd_write)
