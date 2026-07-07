"""Compose the daily digest markdown from DB state for one day."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

from trader import params as params_mod
from trader.digest import data as data_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
DIGEST_DIR = REPO_ROOT / "logs" / "digest"
NOTIFY_SCRIPT = REPO_ROOT / "ops" / "notify.sh"


def _money(value) -> str:
    return f"${value:,.2f}"


def compose_digest(session, day: date) -> str:
    ev = data_mod.load_day_events(session, day)
    pnl = data_mod.realized_pnl_by_sleeve(session, day)
    positions = data_mod.open_positions(session, day)
    params = params_mod.current(session)

    lines = [f"# Daily digest — {day.isoformat()}", ""]

    # --- Kill-switch / halts -------------------------------------------------
    lines.append("## Status")
    lines.append("")
    if ev.halted_sleeves:
        for sleeve in ev.halted_sleeves:
            lines.append(f"- **HALTED**: {sleeve.type} sleeve (HWM {sleeve.hwm})")
    else:
        lines.append("- All sleeves active; no halts in effect.")
    lines.append("")

    # --- P&L by sleeve --------------------------------------------------------
    lines.append("## P&L by sleeve (day cash flow: sells − buys)")
    lines.append("")
    if pnl:
        for sleeve_type in sorted(pnl):
            lines.append(f"- {sleeve_type}: {_money(pnl[sleeve_type])}")
    else:
        lines.append("- No fills today.")
    lines.append("")

    # --- Open positions vs theses ----------------------------------------------
    lines.append("## Open positions vs theses")
    lines.append("")
    if positions:
        lines.append("| Sleeve | Symbol | Qty | Basis | Last | Unreal P&L | Thesis entry | Thesis exit |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for pos in positions:
            last = _money(pos.current_price) if pos.current_price is not None else "n/a"
            upnl = _money(pos.unrealized_pnl) if pos.unrealized_pnl is not None else "n/a"
            entry = pos.thesis.entry if pos.thesis else "—"
            exit_ = pos.thesis.exit if pos.thesis else "—"
            lines.append(
                f"| {pos.sleeve_type} | {pos.symbol} | {data_mod.fmt_dec(pos.qty)} "
                f"| {_money(pos.cost_basis)} | {last} | {upnl} | {entry} | {exit_} |"
            )
    else:
        lines.append("- No open positions.")
    lines.append("")

    # --- Today's trades ----------------------------------------------------------
    lines.append("## Today's trades")
    lines.append("")
    traded = [o for o in ev.orders if o.status != "rejected"]
    if traded:
        grades_by_thesis = {g.thesis_id: g for g in ev.grades}
        for order in traded:
            thesis = order.thesis
            symbol = thesis.symbol if thesis else "?"
            grade = grades_by_thesis.get(order.thesis_id) if order.thesis_id else None
            grade_txt = f" — grade {data_mod.fmt_dec(grade.score)}" if grade else ""
            qty = (
                data_mod.fmt_dec(order.qty)
                if order.qty is not None
                else f"notional {data_mod.fmt_dec(order.notional)}"
            )
            lines.append(
                f"- {order.side} {symbol} qty {qty} [{order.status}] ref {order.ref_id}{grade_txt}"
            )
    else:
        lines.append("- No trades today.")
    lines.append("")

    # --- Gate rejections ------------------------------------------------------------
    lines.append("## Gate rejections")
    lines.append("")
    if ev.gate_rejections:
        for order in ev.gate_rejections:
            symbol = order.thesis.symbol if order.thesis else "?"
            reason = (order.gate_verdict or {}).get("reason", "no reason recorded")
            lines.append(f"- {order.side} {symbol} ref {order.ref_id}: {reason}")
    else:
        lines.append("- None.")
    lines.append("")

    # --- Params in effect --------------------------------------------------------------
    lines.append("## Params in effect")
    lines.append("")
    for name in sorted(params):
        lines.append(f"- {name}: {params[name]}")
    lines.append("")

    return "\n".join(lines)


def write_digest(
    markdown: str,
    day: date,
    *,
    digest_dir: Path | None = None,
    notify: bool = True,
) -> Path:
    """Write the digest file and fire ops/notify.sh if it exists."""
    out_dir = digest_dir or DIGEST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.md"
    path.write_text(markdown)

    if notify and NOTIFY_SCRIPT.exists():
        try:
            subprocess.run(
                [str(NOTIFY_SCRIPT), f"Daily digest ready: {day.isoformat()}", str(path)],
                check=False,
                timeout=30,
            )
        except Exception:
            pass  # the digest itself succeeded; notification is best-effort
    return path
