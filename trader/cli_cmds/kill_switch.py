"""`trader kill-switch ...` — feed equity in, inspect halt state."""

from __future__ import annotations

import argparse


def _print_status(st) -> int:
    def fmt(v, pct=False):
        if v is None:
            return "unknown"
        return f"{v:.1%}" if pct else f"${v:,.2f}"

    print(f"account: equity={fmt(st.equity)} hwm={fmt(st.hwm)} drawdown={fmt(st.drawdown, pct=True)}")
    state = "HALTED" if st.account_halted else "ok"
    print(f"account kill-switch: {state} — {st.reason}")
    for s in st.sleeves:
        state = "HALTED" if s.halted else "ok"
        print(
            f"sleeve {s.type}: {state} equity={fmt(s.equity)} hwm={fmt(s.hwm)} "
            f"drawdown={fmt(s.drawdown, pct=True)} halt_at={s.halt_fraction:.0%} — {s.reason}"
        )
    return 1 if st.account_halted or any(s.halted for s in st.sleeves) else 0


def cmd_status(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.gates import kill_switch

    with get_session() as session:
        return _print_status(kill_switch.status(session))


def cmd_update(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.gates import kill_switch

    sleeve_values = {}
    if args.equity_sleeve is not None:
        sleeve_values["equity"] = args.equity_sleeve
    if args.options_sleeve is not None:
        sleeve_values["options"] = args.options_sleeve

    with get_session() as session:
        st = kill_switch.update(session, args.equity, sleeve_values or None)
        return _print_status(st)


def configure(subparsers) -> None:
    p = subparsers.add_parser("kill-switch", help="account/sleeve kill-switch state")
    sub = p.add_subparsers(dest="kill_switch_command", required=True)

    status = sub.add_parser("status", help="show halt state (exit 1 when anything is halted)")
    status.set_defaults(func=cmd_status)

    update = sub.add_parser("update", help="feed latest portfolio equity (ratchets HWM)")
    update.add_argument("--equity", type=float, required=True, help="total account equity")
    update.add_argument("--equity-sleeve", type=float, help="current equity-sleeve value")
    update.add_argument("--options-sleeve", type=float, help="current options-sleeve value")
    update.set_defaults(func=cmd_update)
