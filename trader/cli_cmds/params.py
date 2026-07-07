"""`trader params ...` — inspect and set tunable runtime parameters.

`params set` is the only write path lanes have to tunables: it validates
the value against the hard envelope (rejecting, never clamping) and appends
a ``param_history`` row carrying the actor and the evidence for the change.
"""

from __future__ import annotations

import argparse
import sys

from trader import params as params_mod


def cmd_show(args: argparse.Namespace) -> int:
    if args.defaults:
        values = params_mod.defaults()
        source = "envelope defaults"
    else:
        try:
            from trader.db.session import get_session

            with get_session() as session:
                values = params_mod.current(session)
            source = "database (latest param_history, else default)"
        except Exception as exc:  # DB unreachable — fall back to defaults
            values = params_mod.defaults()
            source = f"envelope defaults (database unavailable: {exc.__class__.__name__})"

    print(f"# source: {source}")
    width = max(len(n) for n in values)
    for name in sorted(values):
        spec = params_mod.SPECS[name]
        print(f"{name:<{width}}  {values[name]:<8}  envelope [{spec.min}, {spec.max}]")
    return 0


def set_param(session, name: str, value: float, *, evidence: str, actor: str) -> tuple[float, float]:
    """Validate against the envelope and append a param_history row.

    Returns ``(old_value, new_value)``. Raises
    :class:`params_mod.EnvelopeViolation` on unknown params or out-of-bounds
    values, ``ValueError`` when no account row exists.
    """
    from trader.db.models import ParamHistory
    from trader.gates import runtime

    validated = params_mod.validate(name, value)  # raises EnvelopeViolation
    account = runtime.get_account(session)
    if account is None:
        raise ValueError("no account row — run `trader sleeves init` first")
    old = params_mod.current(session)[name]
    session.add(
        ParamHistory(
            account_id=account.id,
            param_name=name,
            old_value=str(old),
            new_value=str(validated),
            evidence=evidence,
            actor=actor,
        )
    )
    session.commit()
    return old, validated


def cmd_set(args: argparse.Namespace) -> int:
    from trader.db.session import get_session

    try:
        value = float(args.value)
    except ValueError:
        print(f"error: value {args.value!r} is not a number", file=sys.stderr)
        return 2

    try:
        with get_session() as session:
            old, new = set_param(
                session, args.name, value, evidence=args.evidence, actor=args.actor
            )
    except params_mod.EnvelopeViolation as exc:
        print(f"ENVELOPE REJECTED: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"{args.name}: {old} -> {new} (actor={args.actor})")
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("params", help="tunable runtime parameters")
    sub = p.add_subparsers(dest="params_command", required=True)

    show = sub.add_parser("show", help="show current param values and envelope bounds")
    show.add_argument("--defaults", action="store_true", help="show envelope defaults only")
    show.set_defaults(func=cmd_show)

    setp = sub.add_parser("set", help="set a param (envelope-validated, recorded in param_history)")
    setp.add_argument("name", choices=sorted(params_mod.SPECS))
    setp.add_argument("value")
    setp.add_argument("--evidence", required=True, help="why this change (recorded)")
    setp.add_argument("--actor", default="improve", help="who is changing it (default: improve)")
    setp.set_defaults(func=cmd_set)
