"""`trader ramp ...` — deterministic live-launch ramp (human-invoked).

Week 1 runs at half caps: per-position ~2.5%, max 3 concurrent positions,
equity sleeve only for days 1-2 (the options sleeve is latched halted, so
the option gate denies everything until `ramp options-on`). After 5 clean
trading days (no gate bugs, reconciliation matches broker), `ramp full`
restores the envelope defaults.

Every param change goes through the same envelope-validated path as
`trader params set` and lands in ``param_history``; the sleeve latch uses
the existing kill-switch halt flag the gates already enforce.
"""

from __future__ import annotations

import argparse
import sys

from trader import params as params_mod

RAMP_PARAMS = {
    "per_position_max_fraction": 0.025,
    "max_concurrent_positions": 3,
}

FULL_PARAMS = {
    "per_position_max_fraction": params_mod.SPECS["per_position_max_fraction"].default,
    "max_concurrent_positions": params_mod.SPECS["max_concurrent_positions"].default,
}


def _options_sleeve(session):
    from trader.gates import runtime

    account = runtime.get_account(session)
    if account is None:
        raise ValueError("no account row — run `trader sleeves init` first")
    for sleeve in account.sleeves:
        if sleeve.type == "options":
            return sleeve
    raise ValueError("no options sleeve — run `trader sleeves init` first")


def _apply(session, values: dict[str, float], evidence: str) -> None:
    from trader.cli_cmds.params import set_param

    for name, value in values.items():
        old, new = set_param(session, name, value, evidence=evidence, actor="human")
        print(f"{name}: {old} -> {new}")


def cmd_start(args: argparse.Namespace) -> int:
    from trader.db.session import get_session

    evidence = "week-1 live ramp: half caps (per plan M5)"
    try:
        with get_session() as session:
            _apply(session, RAMP_PARAMS, evidence)
            sleeve = _options_sleeve(session)
            sleeve.halted = True
            session.commit()
    except (params_mod.EnvelopeViolation, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("options sleeve: HALTED (equity-only; run `trader ramp options-on` on day 3)")
    print("ramp week-1 half caps active")
    return 0


def cmd_options_on(args: argparse.Namespace) -> int:
    from trader.db.session import get_session

    try:
        with get_session() as session:
            sleeve = _options_sleeve(session)
            sleeve.halted = False
            session.commit()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("options sleeve: un-halted (options trading enabled, still at half caps)")
    return 0


def cmd_full(args: argparse.Namespace) -> int:
    from trader.db.session import get_session

    evidence = "ramp complete: 5 clean trading days (no gate bugs, reconciliation clean)"
    try:
        with get_session() as session:
            _apply(session, FULL_PARAMS, evidence)
            sleeve = _options_sleeve(session)
            if sleeve.halted:
                sleeve.halted = False
                session.commit()
                print("options sleeve: un-halted")
    except (params_mod.EnvelopeViolation, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("ramp complete: full envelope defaults restored")
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("ramp", help="live-launch ramp: half caps -> full envelope")
    sub = p.add_subparsers(dest="ramp_command", required=True)

    start = sub.add_parser(
        "start", help="week-1 half caps (2.5%% per position, 3 positions, equity-only)"
    )
    start.set_defaults(func=cmd_start)

    options_on = sub.add_parser("options-on", help="enable the options sleeve (day 3)")
    options_on.set_defaults(func=cmd_options_on)

    full = sub.add_parser("full", help="restore envelope defaults after 5 clean days")
    full.set_defaults(func=cmd_full)
