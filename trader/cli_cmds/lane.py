"""`trader lane ...` — lane-run recording and artifact handoff.

Lanes are headless Claude Code runs. They hand structured JSON artifacts to
each other through Postgres (``lane_runs.artifact``) and record their
lifecycle in ``lane_runs`` so the runner (``ops/run-lane.sh``) can verify a
lane actually completed instead of trusting exit codes.

Artifacts are also mirrored to ``state/artifacts/YYYY-MM-DD/<lane>.json``
(gitignored, ET trading date) for debuggability.

Commands:
    trader lane ping                       exit 0 iff the database answers
    trader lane record-start <lane>        create a running lane_runs row, print its id
    trader lane record-end <run_id> --status completed|failed [--summary ...]
    trader lane artifact put <lane> [--run-id N] [--file f.json | stdin]
    trader lane artifact get <lane> [--date YYYY-MM-DD]
    trader lane check <lane> [--date YYYY-MM-DD]   exit 0 iff a completed run exists
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LANES = ("research", "thesis", "risk", "execution", "review", "improve")
REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = REPO_ROOT / "state" / "artifacts"


def _session(args: argparse.Namespace):
    """Session factory; tests may inject one via ``args.session``."""
    injected = getattr(args, "session", None)
    if injected is not None:
        return injected
    from trader.db.session import get_session

    return get_session()


def _default_account_id(session) -> int:
    from sqlalchemy import select

    from trader.db.models import Account

    account = session.execute(select(Account).order_by(Account.id)).scalars().first()
    if account is None:
        account = Account(name="personal")
        session.add(account)
        session.flush()
    return account.id


def _et_day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=ET)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def _parse_date(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(ET).date()


def _runs_for(session, lane: str, day: date, status: str | None = None):
    from sqlalchemy import select

    from trader.db.models import LaneRun

    lo, hi = _et_day_bounds_utc(day)
    stmt = select(LaneRun).where(LaneRun.lane == lane).order_by(
        LaneRun.started_at.desc(), LaneRun.id.desc()
    )
    if status is not None:
        stmt = stmt.where(LaneRun.status == status)
    rows = session.execute(stmt).scalars().all()
    # SQLite stores naive datetimes; compare in UTC either way.
    out = []
    for row in rows:
        started = row.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if lo <= started < hi:
            out.append(row)
    return out


def cmd_ping(args: argparse.Namespace) -> int:
    from sqlalchemy import text

    try:
        session = _session(args)
        session.execute(text("SELECT 1"))
    except Exception as exc:
        print(f"database unreachable: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1
    print("ok")
    return 0


def cmd_record_start(args: argparse.Namespace) -> int:
    from trader.db.models import LaneRun

    session = _session(args)
    run = LaneRun(account_id=_default_account_id(session), lane=args.lane, status="running")
    session.add(run)
    session.commit()
    print(run.id)
    return 0


def cmd_record_end(args: argparse.Namespace) -> int:
    from trader.db.models import LaneRun, utcnow

    session = _session(args)
    run = session.get(LaneRun, args.run_id)
    if run is None:
        print(f"no lane run with id {args.run_id}", file=sys.stderr)
        return 1
    run.status = args.status
    run.finished_at = utcnow()
    if args.summary:
        run.summary = args.summary
    session.commit()
    return 0


def cmd_artifact_put(args: argparse.Namespace) -> int:
    from trader.db.models import LaneRun

    raw = Path(args.file).read_text() if args.file else sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"artifact is not valid JSON: {exc}", file=sys.stderr)
        return 1

    session = _session(args)
    if args.run_id is not None:
        run = session.get(LaneRun, args.run_id)
    else:
        runs = _runs_for(session, args.lane, _parse_date(None))
        run = runs[0] if runs else None
    if run is None:
        print(f"no lane run found for {args.lane}", file=sys.stderr)
        return 1
    if run.lane != args.lane:
        print(f"run {run.id} belongs to lane {run.lane!r}, not {args.lane!r}", file=sys.stderr)
        return 1
    run.artifact = payload
    session.commit()

    day = datetime.now(ET).date().isoformat()
    mirror_root = Path(getattr(args, "artifact_root", None) or ARTIFACT_ROOT)
    mirror = mirror_root / day / f"{args.lane}.json"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"stored artifact on run {run.id}; mirrored to {mirror}")
    return 0


def cmd_artifact_get(args: argparse.Namespace) -> int:
    session = _session(args)
    day = _parse_date(args.date)
    runs = _runs_for(session, args.lane, day)
    for run in runs:  # newest first; prefer completed runs with artifacts
        if run.artifact is not None and run.status == "completed":
            print(json.dumps(run.artifact, indent=2))
            return 0
    for run in runs:
        if run.artifact is not None:
            print(json.dumps(run.artifact, indent=2))
            return 0
    print(f"no artifact for lane {args.lane!r} on {day}", file=sys.stderr)
    return 1


def cmd_check(args: argparse.Namespace) -> int:
    session = _session(args)
    day = _parse_date(args.date)
    runs = _runs_for(session, args.lane, day, status="completed")
    if runs:
        print(runs[0].id)
        return 0
    print(f"no completed {args.lane} run on {day}", file=sys.stderr)
    return 1


def configure(subparsers) -> None:
    lane = subparsers.add_parser("lane", help="lane-run recording and artifact handoff")
    sub = lane.add_subparsers(dest="lane_command", required=True)

    ping = sub.add_parser("ping", help="exit 0 iff the database is reachable")
    ping.set_defaults(func=cmd_ping)

    start = sub.add_parser("record-start", help="record a lane run start, print run id")
    start.add_argument("lane", choices=LANES)
    start.set_defaults(func=cmd_record_start)

    end = sub.add_parser("record-end", help="record a lane run end")
    end.add_argument("run_id", type=int)
    end.add_argument("--status", choices=("completed", "failed"), required=True)
    end.add_argument("--summary", default=None)
    end.set_defaults(func=cmd_record_end)

    artifact = sub.add_parser("artifact", help="store/retrieve lane artifacts (JSON)")
    art_sub = artifact.add_subparsers(dest="artifact_command", required=True)

    put = art_sub.add_parser("put", help="attach a JSON artifact to a lane run")
    put.add_argument("lane", choices=LANES)
    put.add_argument("--run-id", type=int, default=None, help="default: today's latest run")
    put.add_argument("--file", default=None, help="JSON file (default: stdin)")
    put.set_defaults(func=cmd_artifact_put)

    get = art_sub.add_parser("get", help="print the latest artifact for a lane")
    get.add_argument("lane", choices=LANES)
    get.add_argument("--date", default=None, help="ET date YYYY-MM-DD (default: today)")
    get.set_defaults(func=cmd_artifact_get)

    check = sub.add_parser("check", help="exit 0 iff a completed run exists for the lane")
    check.add_argument("lane", choices=LANES)
    check.add_argument("--date", default=None, help="ET date YYYY-MM-DD (default: today)")
    check.set_defaults(func=cmd_check)
