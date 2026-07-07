"""`trader dry-run ...` — inspect/flip the dry_run flag (DB param).

Dry run defaults to ON when no param_history row exists; both gates then
deny live placement (recording simulated orders). M5 flips it off for the
live ramp. The flag is stored as a ``param_history`` row so every flip is
audited with an actor and timestamp.
"""

from __future__ import annotations

import argparse


def cmd_status(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.gates import runtime

    with get_session() as session:
        enabled = runtime.dry_run_enabled(session)
    print(f"dry_run: {'ON (orders simulated, not placed)' if enabled else 'OFF (live)'}")
    return 0


def cmd_set(args: argparse.Namespace, value: str) -> int:
    from trader.db.models import ParamHistory
    from trader.db.session import get_session
    from trader.gates import runtime

    with get_session() as session:
        account = runtime.get_account(session)
        if account is None:
            print("no account row — run `trader sleeves init` first")
            return 1
        old = "1" if runtime.dry_run_enabled(session) else "0"
        session.add(
            ParamHistory(
                account_id=account.id,
                param_name=runtime.DRY_RUN_PARAM,
                old_value=old,
                new_value=value,
                evidence=args.reason,
                actor=args.actor,
            )
        )
        session.commit()
    print(f"dry_run set to {'ON' if value == '1' else 'OFF'}")
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("dry-run", help="dry-run flag (simulate instead of place)")
    sub = p.add_subparsers(dest="dry_run_command", required=True)

    status = sub.add_parser("status", help="show current dry_run state")
    status.set_defaults(func=cmd_status)

    for name, value, help_text in (
        ("on", "1", "enable dry run (gates simulate orders)"),
        ("off", "0", "disable dry run (gates allow live placement)"),
    ):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--reason", help="why this flip happened (recorded as evidence)")
        cmd.add_argument("--actor", default="human", help="who flipped it (default: human)")
        cmd.set_defaults(func=lambda args, v=value: cmd_set(args, v))
