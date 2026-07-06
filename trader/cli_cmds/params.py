"""`trader params ...` — inspect tunable runtime parameters."""

from __future__ import annotations

import argparse

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


def configure(subparsers) -> None:
    p = subparsers.add_parser("params", help="tunable runtime parameters")
    sub = p.add_subparsers(dest="params_command", required=True)

    show = sub.add_parser("show", help="show current param values and envelope bounds")
    show.add_argument("--defaults", action="store_true", help="show envelope defaults only")
    show.set_defaults(func=cmd_show)
