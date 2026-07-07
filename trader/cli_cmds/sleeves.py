"""`trader sleeves ...` — sleeve ledger: init, budgets, exposure, P&L."""

from __future__ import annotations

import argparse


def cmd_init(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.sleeves import ledger

    with get_session() as session:
        account = ledger.init_sleeves(session)
        print(f"account {account.name!r} (id={account.id})")
        for sleeve in sorted(account.sleeves, key=lambda s: s.type):
            print(
                f"sleeve {sleeve.type}: budget {float(sleeve.budget_fraction):.0%} "
                f"halt_at {float(sleeve.drawdown_halt_fraction):.0%}"
            )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from trader.db.session import get_session
    from trader.gates import runtime
    from trader.sleeves import ledger

    with get_session() as session:
        account = runtime.get_account(session)
        if account is None:
            print("no account row — run `trader sleeves init` first")
            return 1
        equity = f"${float(account.equity):,.2f}" if account.equity is not None else "unknown"
        print(f"account {account.name!r}: equity {equity}")
        for sleeve in sorted(account.sleeves, key=lambda s: s.type):
            r = ledger.sleeve_report(session, account, sleeve)
            budget = f"${r.budget_dollars:,.2f}" if r.budget_dollars is not None else "unknown"
            remaining = f"${r.remaining_budget:,.2f}" if r.remaining_budget is not None else "unknown"
            state = "HALTED" if r.halted else "ok"
            print(
                f"sleeve {r.type} [{state}]: budget {r.budget_fraction:.0%} ({budget}) "
                f"exposure ${r.open_exposure:,.2f} pending ${r.pending_exposure:,.2f} "
                f"remaining {remaining} realized P&L ${r.realized_pnl:,.2f}"
            )
            for pos in r.positions:
                print(
                    f"  {pos.key}: qty {pos.qty} avg ${float(pos.avg_price):,.2f} "
                    f"cost ${float(pos.cost_basis):,.2f}"
                )
    return 0


def configure(subparsers) -> None:
    p = subparsers.add_parser("sleeves", help="sleeve ledger")
    sub = p.add_subparsers(dest="sleeves_command", required=True)

    init = sub.add_parser("init", help="create account row + equity/options sleeves")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="budgets, exposure, P&L, halted state")
    status.set_defaults(func=cmd_status)
