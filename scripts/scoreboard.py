"""Print the paper-fleet scoreboard: one row per strategy, ranked by return.

Usage: uv run scripts/scoreboard.py [--json]

Reads state/paper.json (written by run_strategies.py). This is the view that
decides which strategy earns promotion to live trading.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paper

ROOT = Path(__file__).parent.parent
PAPER_PATH = ROOT / "state" / "paper.json"


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

    if args.json:
        print(json.dumps({"as_of": state.get("last_run_date"), "rows": rows},
                         indent=2))
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


if __name__ == "__main__":
    main()
