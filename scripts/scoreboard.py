"""Print the paper-fleet scoreboard: one row per strategy, ranked by return.

Usage: uv run scripts/scoreboard.py [--json]

Reads state/paper.json (written by run_strategies.py). This is the view that
decides which strategy earns promotion to live trading. Also surfaces the
allocator's current champion and recent pick history from untracked
state/allocator.json (written by `allocate.py --pick --record`) when it
exists — the allocator is recommend-only, so the scoreboard degrades
gracefully without it.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper

ROOT = Path(__file__).parent.parent
PAPER_PATH = ROOT / "state" / "paper.json"
ALLOC_PATH = ROOT / "state" / "allocator.json"
ALLOC_RECENT = 10  # picks shown in the recent-history view


def load_picks(path) -> list:
    """Allocator pick history, oldest first; [] when the file is missing,
    unreadable, or empty — the allocator is optional and the scoreboard must
    never fail because of it."""
    if not path.exists():
        return []
    try:
        history = json.loads(path.read_text())
    except json.JSONDecodeError:
        return []
    picks = history.get("picks") if isinstance(history, dict) else None
    return picks if isinstance(picks, list) else []


def allocator_lines(picks: list, recent: int = ALLOC_RECENT) -> list:
    """Render the current champion and the last `recent` picks, marking the
    days the champion switched. Empty list when there is no history."""
    if not picks:
        return []
    cur = picks[-1]
    score = (cur.get("scores") or {}).get(cur["champion"])
    weight = (cur.get("weights") or {}).get(cur["champion"])
    score_s = f"{score:.2f}" if score is not None else "n/a"
    weight_s = f"{weight:.4f}" if weight is not None else "n/a"
    lines = [f"allocator champion (recommend-only): {cur['champion']} "
             f"(score {score_s}, weight {weight_s}) as of {cur['date']}",
             f"recent picks (last {min(recent, len(picks))}):"]
    window = picks[-recent:]
    prev = picks[-recent - 1]["champion"] if len(picks) > recent else None
    for pick in window:
        switch = ("  <- switch"
                  if prev is not None and pick["champion"] != prev else "")
        lines.append(f"  {pick['date']}  {pick['champion']}{switch}")
        prev = pick["champion"]
    return lines


def position_summary(book: dict) -> str:
    pos = book["position"]
    if not pos:
        return "-"
    if pos["kind"] == "equity":
        return f"{pos['shares']:.2f} {pos['symbol']} @ {pos['entry_price']:.2f}"
    return (f"{pos['contracts']}x {pos['underlying']} {pos['expiry']} "
            f"{pos['strike']:g}{pos['right'][0].upper()} @ {pos['entry_premium']:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    if not PAPER_PATH.exists():
        print("no paper state yet — run scripts/run_strategies.py first")
        sys.exit(1)
    state = json.loads(PAPER_PATH.read_text())

    rows = []
    for name, book in state["books"].items():
        s = paper.stats(book)
        rows.append({"strategy": name, **s, "position": position_summary(book)})
    rows.sort(key=lambda r: r["total_return"], reverse=True)

    picks = load_picks(ALLOC_PATH)

    if args.json:
        alloc = ({"champion": picks[-1]["champion"],
                  "picks": picks[-ALLOC_RECENT:]} if picks else None)
        print(json.dumps({"as_of": state.get("last_run_date"), "rows": rows,
                          "allocator": alloc}, indent=2))
        return

    start = state["books"][rows[0]["strategy"]]["starting_cash"] if rows else 0
    print(f"paper fleet as of {state.get('last_run_date')} "
          f"(start ${start:,.0f}/book)\n")
    header = (f"{'strategy':<24} {'value':>10} {'return':>8} {'maxDD':>7} "
              f"{'trades':>6} {'win%':>5} {'days':>4}  position")
    print(header)
    print("-" * len(header))
    for r in rows:
        win = f"{r['win_rate']:.0%}" if r["win_rate"] is not None else "-"
        print(f"{r['strategy']:<24} {r['value']:>10,.2f} {r['total_return']:>8.2%} "
              f"{r['max_drawdown']:>7.2%} {r['trades']:>6} {win:>5} {r['days']:>4}"
              f"  {r['position']}")

    lines = allocator_lines(picks)
    if lines:
        print()
        print("\n".join(lines))


if __name__ == "__main__":
    main()
