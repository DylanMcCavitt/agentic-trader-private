"""Rank the paper fleet by exponentially decayed Sharpe of daily book returns.

Usage: uv run scripts/allocate.py [--json] [--half-life DAYS]

Allocator slice 1: the scoring foundation of the bandit allocator. Reads
every strategy's paper book from state/paper.json (written by
run_strategies.py), computes daily returns from each book's marked-to-market
history, and scores them with a recency-weighted (exponentially decayed)
Sharpe ratio so a losing streak erodes a strategy's rank within days.

Books with fewer than MIN_RETURNS daily returns are flagged "insufficient"
and ranked last — never given a fake score. The decayed mean, decayed std,
and effective sample size (n_eff) are exposed for reuse by later slices
(Thompson sampling needs all three).

Read-only: never touches config, order gates, or any live trading path.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PAPER_PATH = ROOT / "state" / "paper.json"

HALF_LIFE_DAYS = 63    # decay half-life in trading days (~one quarter)
MIN_RETURNS = 20       # minimum daily returns before a score is trusted
TRADING_DAYS = 252     # annualization base


# --- pure scoring functions (reused by allocator slices 2-4) -----------------

def daily_returns(history: list[dict]) -> list[float]:
    """Day-over-day returns of a book's marked-to-market value series."""
    values = [h["value"] for h in history]
    return [values[i] / values[i - 1] - 1.0
            for i in range(1, len(values)) if values[i - 1]]


def decay_weights(n: int, half_life: float = HALF_LIFE_DAYS) -> list[float]:
    """Exponential decay weights, oldest first; the newest weight is 1 and a
    return half_life days older weighs half as much."""
    return [0.5 ** ((n - 1 - i) / half_life) for i in range(n)]


def decayed_stats(returns: list[float],
                  half_life: float = HALF_LIFE_DAYS) -> dict:
    """Decayed mean / variance / std and effective sample size
    n_eff = (sum w)^2 / sum w^2 over the same weights."""
    w = decay_weights(len(returns), half_life)
    sw = sum(w)
    mean = sum(wi * r for wi, r in zip(w, returns)) / sw
    var = sum(wi * (r - mean) ** 2 for wi, r in zip(w, returns)) / sw
    return {"mean": mean, "var": var, "std": math.sqrt(var),
            "n_eff": sw ** 2 / sum(wi * wi for wi in w)}


def decayed_sharpe(returns: list[float],
                   half_life: float = HALF_LIFE_DAYS) -> float:
    """Annualized decayed Sharpe: decayed mean / decayed std * sqrt(252).
    A flat book (zero std) scores 0.0 — no evidence either way."""
    s = decayed_stats(returns, half_life)
    if s["std"] == 0.0:
        return 0.0
    return s["mean"] / s["std"] * math.sqrt(TRADING_DAYS)


def score_book(book: dict, half_life: float = HALF_LIFE_DAYS,
               min_returns: int = MIN_RETURNS) -> dict:
    """Score one paper book. Books with fewer than min_returns daily returns
    are flagged insufficient and carry no score."""
    rets = daily_returns(book.get("history", []))
    if len(rets) < min_returns:
        return {"score": None, "mean": None, "std": None, "n_eff": None,
                "days": len(rets), "insufficient": True}
    s = decayed_stats(rets, half_life)
    return {"score": decayed_sharpe(rets, half_life),
            "mean": s["mean"], "std": s["std"], "n_eff": s["n_eff"],
            "days": len(rets), "insufficient": False}


def rank_books(books: dict, half_life: float = HALF_LIFE_DAYS,
               min_returns: int = MIN_RETURNS) -> list[dict]:
    """Rank all books by decayed Sharpe, insufficient-data books last."""
    rows = [{"strategy": name, **score_book(book, half_life, min_returns)}
            for name, book in books.items()]
    rows.sort(key=lambda r: (r["insufficient"],
                             -r["score"] if r["score"] is not None else 0.0,
                             r["strategy"]))
    return rows


# --- CLI ----------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--half-life", type=float, default=HALF_LIFE_DAYS,
                    help=f"decay half-life in trading days (default {HALF_LIFE_DAYS})")
    args = ap.parse_args()

    if not PAPER_PATH.exists():
        print(f"no paper state at {PAPER_PATH} — run scripts/run_strategies.py first",
              file=sys.stderr)
        sys.exit(1)
    try:
        state = json.loads(PAPER_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"unreadable paper state at {PAPER_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)

    rows = rank_books(state.get("books", {}), half_life=args.half_life)

    if args.json:
        out = [{**r,
                "score": round(r["score"], 4) if r["score"] is not None else None,
                "mean": round(r["mean"], 6) if r["mean"] is not None else None,
                "std": round(r["std"], 6) if r["std"] is not None else None,
                "n_eff": round(r["n_eff"], 2) if r["n_eff"] is not None else None}
               for r in rows]
        print(json.dumps({"as_of": state.get("last_run_date"),
                          "half_life_days": args.half_life,
                          "min_returns": MIN_RETURNS, "rows": out}, indent=2))
        return

    print(f"allocator ranking as of {state.get('last_run_date')} "
          f"(half-life {args.half_life:g}d, min {MIN_RETURNS} daily returns)\n")
    header = (f"{'#':>2} {'strategy':<24} {'score':>8} {'mean/d':>9} "
              f"{'std/d':>8} {'n_eff':>6} {'days':>4}  note")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows, 1):
        if r["insufficient"]:
            print(f"{i:>2} {r['strategy']:<24} {'-':>8} {'-':>9} {'-':>8} "
                  f"{'-':>6} {r['days']:>4}  insufficient data "
                  f"(< {MIN_RETURNS} daily returns)")
        else:
            print(f"{i:>2} {r['strategy']:<24} {r['score']:>8.2f} "
                  f"{r['mean']:>9.5f} {r['std']:>8.5f} {r['n_eff']:>6.1f} "
                  f"{r['days']:>4}")


if __name__ == "__main__":
    main()
