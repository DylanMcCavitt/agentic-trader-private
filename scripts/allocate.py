"""Rank the paper fleet by exponentially decayed Sharpe of daily book returns.

Usage: uv run scripts/allocate.py [--json] [--half-life DAYS]
       uv run scripts/allocate.py --pick [--date YYYY-MM-DD] [--seed N] [--json]

Allocator slice 1: the scoring foundation of the bandit allocator. Reads
every strategy's paper book from state/paper.json (written by
run_strategies.py), computes daily returns from each book's marked-to-market
history, and scores them with a recency-weighted (exponentially decayed)
Sharpe ratio so a losing streak erodes a strategy's rank within days.

Books with fewer than MIN_RETURNS daily returns are flagged "insufficient"
and ranked last — never given a fake score. The decayed mean, decayed std,
and effective sample size (n_eff) are exposed for reuse by later slices
(Thompson sampling needs all three).

Allocator slice 2 (--pick): Thompson-sampling champion selection. Each
strategy's skill (true daily mean return) gets a Normal posterior
N(decayed_mean, decayed_var / n_eff) — tight when evidence is long, wide
when n_eff is small, so sparse books still explore. Insufficient-data books
stay eligible under a diffuse prior N(0, PRIOR_STD^2) and are marked as
such. One draw per strategy, seeded deterministically from the pick date
(+ optional --seed offset) and the strategy name; the highest draw is the
day's champion. Weights are rank-normalized over the draws (Borda: best of
n gets n/(n(n+1)/2), worst gets 1/(n(n+1)/2)) — draws live on a ~1e-3 daily
return scale where a raw softmax would be indistinguishable from uniform.

Recommend-only: never touches config, order gates, or any live trading path.
"""
import argparse
import datetime as dt
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
PAPER_PATH = ROOT / "state" / "paper.json"

HALF_LIFE_DAYS = 63    # decay half-life in trading days (~one quarter)
MIN_RETURNS = 20       # minimum daily returns before a score is trusted
TRADING_DAYS = 252     # annualization base
STD_FLOOR = 1e-12      # below this, std is float noise — treat as flat book
PRIOR_STD = 0.01       # diffuse prior std on daily mean return (1%/day) for
                       # insufficient-data books — wide enough to win draws
                       # occasionally, narrow enough not to dominate


# --- pure scoring functions (reused by allocator slices 2-4) -----------------

def daily_returns(history: list[dict]) -> list[float]:
    """Day-over-day returns of a book's marked-to-market value series."""
    values = [h["value"] for h in history]
    return [values[i] / values[i - 1] - 1.0
            for i in range(1, len(values)) if values[i - 1]]


def decay_weights(n: int, half_life: float = HALF_LIFE_DAYS) -> list[float]:
    """Exponential decay weights, oldest first; the newest weight is 1 and a
    return half_life days older weighs half as much."""
    if half_life <= 0:
        raise ValueError(f"half_life must be > 0, got {half_life}")
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
    A flat book (std at or below float-noise level) scores 0.0 — no evidence
    either way. The STD_FLOOR avoids astronomically large Sharpes when a
    constant return series picks up ~1e-18 of float summation jitter."""
    s = decayed_stats(returns, half_life)
    if s["std"] <= STD_FLOOR:
        return 0.0
    return s["mean"] / s["std"] * math.sqrt(TRADING_DAYS)


def score_book(book: dict, half_life: float = HALF_LIFE_DAYS,
               min_returns: int = MIN_RETURNS) -> dict:
    """Score one paper book. Books with fewer than min_returns daily returns
    are flagged insufficient and carry no score. Non-finite returns (NaN/inf
    from corrupted state) are likewise treated as insufficient — json
    round-trips NaN, so a bad upstream mark must never rank first."""
    rets = daily_returns(book.get("history", []))
    if len(rets) < min_returns or not all(math.isfinite(r) for r in rets):
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


# --- Thompson sampling (allocator slice 2) ------------------------------------

def posterior_params(scored: dict, prior_std: float = PRIOR_STD) -> tuple:
    """(mean, std) of the Normal posterior on a strategy's true daily mean
    return. Scored books get the standard normal approximation
    N(decayed_mean, decayed_var / n_eff); insufficient books get the diffuse
    prior N(0, prior_std^2) so they stay eligible and explore.

    A scored book whose decayed std is at or below STD_FLOOR is float-noise
    flat — the same "no evidence either way" convention decayed_sharpe uses
    to score it 0.0 — and also gets the diffuse prior: a ~1e-17 posterior
    std would otherwise be a delta function at the book's mean, winning (or
    losing) every draw with zero exploration."""
    if scored["insufficient"] or scored["std"] <= STD_FLOOR:
        return 0.0, prior_std
    return scored["mean"], math.sqrt(scored["std"] ** 2 / scored["n_eff"])


def pick_seed(date_key: str, seed: int = 0) -> str:
    """Deterministic RNG key prefix from a YYYY-MM-DD pick date and a seed
    offset. The seed is folded in as a separate token rather than added to
    the date integer, so --seed 1 today never reproduces the exact RNG
    stream of seed 0 tomorrow — a seeded re-roll is an independent draw,
    not tomorrow's draws consumed early."""
    return f"{dt.date.fromisoformat(date_key).strftime('%Y%m%d')}:{seed}"


def thompson_pick(books: dict, date_key: str, seed: int = 0,
                  half_life: float = HALF_LIFE_DAYS,
                  min_returns: int = MIN_RETURNS,
                  prior_std: float = PRIOR_STD) -> dict:
    """One Thompson-sampling round over all books: one gaussian draw per
    strategy from its posterior; highest draw is the champion.

    Determinism: each strategy's RNG is random.Random(f"{base}:{name}") —
    a string seed (stdlib hashes it with sha512, immune to PYTHONHASHSEED)
    built from the "YYYYMMDD:seed" key and the strategy name, so the
    result depends only on (date, seed, books) and never on dict insertion
    order. Strategy names are iterated sorted.

    Weights are rank-normalized over the draws (Borda count): with n
    strategies the k-th best draw gets weight (n - k + 1) / (n(n+1)/2).
    """
    rows = []
    base = pick_seed(date_key, seed)
    for name in sorted(books):
        scored = score_book(books[name], half_life, min_returns)
        mean, std = posterior_params(scored, prior_std)
        draw = random.Random(f"{base}:{name}").gauss(mean, std)
        rows.append({"strategy": name, "draw": draw,
                     "post_mean": mean, "post_std": std,
                     "days": scored["days"],
                     "insufficient": scored["insufficient"]})
    rows.sort(key=lambda r: (-r["draw"], r["strategy"]))
    n = len(rows)
    for i, r in enumerate(rows):
        r["weight"] = (n - i) / (n * (n + 1) / 2)
    return {"date": date_key, "seed": seed,
            "champion": rows[0]["strategy"] if rows else None,
            "rows": rows}


# --- CLI ----------------------------------------------------------------------

def print_pick(result: dict, state: dict, args) -> None:
    """Render a thompson_pick result as a table or --json."""
    if args.json:
        rows = [{**r,
                 "draw": round(r["draw"], 8),
                 "weight": round(r["weight"], 6),
                 "post_mean": round(r["post_mean"], 8),
                 "post_std": round(r["post_std"], 8)}
                for r in result["rows"]]
        print(json.dumps({"as_of": state.get("last_run_date"),
                          "date": result["date"], "seed": result["seed"],
                          "half_life_days": args.half_life,
                          "prior_std": PRIOR_STD,
                          "champion": result["champion"],
                          "rows": rows}, indent=2))
        return
    if result["champion"] is None:
        print(f"no books in paper state — nothing to pick for {result['date']}")
        return
    print(f"thompson pick for {result['date']} (seed offset {result['seed']}, "
          f"half-life {args.half_life:g}d)")
    print(f"champion: {result['champion']}\n")
    header = (f"{'#':>2} {'strategy':<24} {'draw':>10} {'weight':>7} "
              f"{'post_mu':>10} {'post_sd':>10} {'days':>4}  note")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(result["rows"], 1):
        note = "insufficient data (diffuse prior)" if r["insufficient"] else ""
        print(f"{i:>2} {r['strategy']:<24} {r['draw']:>10.6f} "
              f"{r['weight']:>7.4f} {r['post_mean']:>10.6f} "
              f"{r['post_std']:>10.6f} {r['days']:>4}  {note}".rstrip())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--half-life", type=float, default=HALF_LIFE_DAYS,
                    help=f"decay half-life in trading days (default {HALF_LIFE_DAYS})")
    ap.add_argument("--pick", action="store_true",
                    help="Thompson-sample a champion for the day")
    ap.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                    help="pick date for RNG seeding (default: today; --pick only)")
    ap.add_argument("--seed", type=int, default=0,
                    help="seed offset added to the date seed (--pick only)")
    args = ap.parse_args()
    if args.half_life <= 0:
        ap.error(f"--half-life must be > 0, got {args.half_life:g}")
    if (args.date is not None or args.seed != 0) and not args.pick:
        ap.error("--date/--seed only apply with --pick")
    pick_date = args.date if args.date is not None else dt.date.today().isoformat()
    if args.pick:
        try:
            # strict YYYY-MM-DD: strptime alone tolerates "2026-6-12" and
            # 3.11+ fromisoformat tolerates "20260612" — require both plus
            # the dashed 10-char shape
            if len(pick_date) != 10:
                raise ValueError(pick_date)
            dt.datetime.strptime(pick_date, "%Y-%m-%d")
        except ValueError:
            ap.error(f"--date must be YYYY-MM-DD, got {pick_date!r}")

    if not PAPER_PATH.exists():
        print(f"no paper state at {PAPER_PATH} — run scripts/run_strategies.py first",
              file=sys.stderr)
        sys.exit(1)
    try:
        state = json.loads(PAPER_PATH.read_text())
    except json.JSONDecodeError as exc:
        print(f"unreadable paper state at {PAPER_PATH}: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.pick:
        result = thompson_pick(state.get("books", {}), pick_date,
                               seed=args.seed, half_life=args.half_life)
        print_pick(result, state, args)
        return

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
