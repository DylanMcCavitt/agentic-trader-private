"""Journal writer: append/replace one day's section in journal/<YYYY>/<MM>.md.

The journal is the git-tracked mirror of what the trader did each day (lane
runs, theses, orders/fills, grades, param changes, halts), so any future
session can answer "what has it been doing?" without database access.

Idempotent: re-running a day replaces that day's section in place. Sections
are ``## YYYY-MM-DD`` headings kept in chronological order.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from trader.digest import data as data_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNAL_DIR = REPO_ROOT / "journal"

_SECTION_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2})$", re.MULTILINE)


def render_day_section(session, day: date) -> str:
    """Render one day's events as a markdown section (deterministic)."""
    ev = data_mod.load_day_events(session, day)
    lines = [f"## {day.isoformat()}", ""]

    if ev.halted_sleeves:
        for sleeve in ev.halted_sleeves:
            lines.append(f"- **HALT**: {sleeve.type} sleeve is halted")
        lines.append("")

    lines.append("### Lane runs")
    if ev.lane_runs:
        for run in ev.lane_runs:
            summary = f" — {run.summary}" if run.summary else ""
            lines.append(f"- {run.lane}: {run.status}{summary}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("### Theses created")
    if ev.theses_created:
        for thesis in ev.theses_created:
            lines.append(
                f"- #{thesis.id} {thesis.symbol} {thesis.direction} {thesis.instrument} "
                f"[{thesis.status}] — entry: {thesis.entry}; exit: {thesis.exit}; "
                f"invalidation: {thesis.invalidation}"
            )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("### Orders and fills")
    if ev.orders or ev.fills:
        fmt = data_mod.fmt_dec
        fills_by_order: dict[int, list] = {}
        for fill in ev.fills:
            fills_by_order.setdefault(fill.order_id, []).append(fill)
        for order in ev.orders:
            symbol = order.thesis.symbol if order.thesis else "?"
            qty = fmt(order.qty) if order.qty is not None else f"notional {fmt(order.notional)}"
            lines.append(f"- {order.side} {symbol} qty {qty} [{order.status}] ref {order.ref_id}")
            for fill in fills_by_order.pop(order.id, []):
                lines.append(f"  - filled {fmt(fill.qty)} @ {fmt(fill.price, 2)}")
        # Fills on orders created on earlier days still belong in today's log.
        for order_id, fills in fills_by_order.items():
            for fill in fills:
                lines.append(
                    f"- fill on order #{order_id}: {fmt(fill.qty)} @ {fmt(fill.price, 2)}"
                )
    else:
        lines.append("- none")
    lines.append("")

    lines.append("### Grades")
    if ev.grades:
        for grade in ev.grades:
            notes = f" — {grade.notes}" if grade.notes else ""
            lines.append(f"- thesis #{grade.thesis_id}: {data_mod.fmt_dec(grade.score)}{notes}")
    else:
        lines.append("- none")
    lines.append("")

    lines.append("### Param changes")
    if ev.param_changes:
        for change in ev.param_changes:
            lines.append(
                f"- {change.param_name}: {change.old_value} -> {change.new_value} "
                f"({change.actor})"
            )
    else:
        lines.append("- none")
    lines.append("")

    return "\n".join(lines)


def _split_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Split journal file text into (preamble, [(date, section_text), ...])."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return text, []
    preamble = text[: matches[0].start()]
    sections = []
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((match.group(1), text[match.start() : end].rstrip() + "\n"))
    return preamble, sections


def upsert_day(session, day: date, *, journal_dir: Path | None = None) -> Path:
    """Write/replace ``day``'s section in the month file. Returns the path."""
    base = journal_dir or JOURNAL_DIR
    path = base / f"{day.year:04d}" / f"{day.month:02d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    new_section = render_day_section(session, day).rstrip() + "\n"
    day_key = day.isoformat()

    if path.exists():
        preamble, sections = _split_sections(path.read_text())
    else:
        preamble = f"# Journal — {day.year:04d}-{day.month:02d}\n\n"
        sections = []

    by_day = {d: s for d, s in sections}
    by_day[day_key] = new_section
    ordered = [by_day[d] for d in sorted(by_day)]

    path.write_text(preamble.rstrip() + "\n\n" + "\n".join(ordered))
    return path
