"""`trader screener ...` — deterministic yfinance momentum screens."""

from __future__ import annotations

import argparse
import json
import sys


def cmd_run(args: argparse.Namespace) -> int:
    from trader.screener.run import run_screener

    report = run_screener(top=args.top, offline_universe=args.offline_universe)
    print(json.dumps(report, indent=2))

    if args.record:
        try:
            _record_run(report)
        except Exception as exc:
            print(f"warning: failed to record screener run: {exc}", file=sys.stderr)

    # Nonzero only on total failure: nothing downloaded at all.
    if report["fetched"] == 0:
        print("error: no market data could be fetched", file=sys.stderr)
        return 1
    return 0


def _record_run(report: dict) -> None:
    from trader.db.models import Account, LaneRun, utcnow
    from trader.db.session import get_session

    with get_session() as session:
        account = session.query(Account).order_by(Account.id).first()
        if account is None:
            account = Account(name="default")
            session.add(account)
            session.flush()
        session.add(
            LaneRun(
                account_id=account.id,
                lane="screener",
                finished_at=utcnow(),
                status="succeeded",
                summary=f"{len(report['candidates'])} candidates from "
                f"{report['fetched']}/{report['universe']['size']} symbols",
                artifact=report,
            )
        )
        session.commit()


def cmd_check(args: argparse.Namespace) -> int:
    from trader.screener.run import check_symbol

    result = check_symbol(args.symbol)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def configure(subparsers) -> None:
    p = subparsers.add_parser("screener", help="momentum/liquidity screens")
    sub = p.add_subparsers(dest="screener_command", required=True)

    run = sub.add_parser("run", help="run all screens, print candidates as JSON")
    run.add_argument("--top", type=int, default=None, help="limit to top N candidates")
    run.add_argument(
        "--record", action="store_true", help="also record the run in the database"
    )
    run.add_argument(
        "--offline-universe",
        action="store_true",
        help="use the committed fallback universe (skip Wikipedia)",
    )
    run.set_defaults(func=cmd_run)

    check = sub.add_parser("check", help="check one symbol against the hard floors")
    check.add_argument("symbol")
    check.set_defaults(func=cmd_check)
