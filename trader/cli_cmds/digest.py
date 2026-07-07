"""`trader digest` — compose and write the daily digest."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime


def _parse_date(value: str | None) -> date:
    if value is None:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def cmd_digest(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.digest.compose import compose_digest, write_digest

    day = _parse_date(args.date)
    try:
        with get_session() as session:
            markdown = compose_digest(session, day)
    except Exception as exc:
        print(f"error: could not compose digest: {exc}", file=sys.stderr)
        return 1

    print(markdown)
    path = write_digest(markdown, day, notify=not args.no_notify)
    print(f"written: {path}", file=sys.stderr)
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("digest", help="compose the daily digest")
    p.add_argument("--date", help="YYYY-MM-DD (default: today)")
    p.add_argument("--no-notify", action="store_true", help="skip ops/notify.sh")
    p.set_defaults(func=cmd_digest)
