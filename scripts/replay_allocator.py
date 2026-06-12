"""Replay the Thompson allocator day-by-day over fleet backtest histories.

Usage: uv run scripts/replay_allocator.py [--start 2005-01-01] [--end ...]
           [--half-life 63] [--seed 0] [--warmup 21] [--books PATH]
           [--iv-premium 1.15] [--opt-slip-pct 0.015] [--rate 0.04] [--json]

Allocator slice 4: evidence the allocator adds value before anyone trusts
its picks. Replays the slice-2 Thompson champion scheme of
scripts/allocate.py over each strategy's daily book values from the fleet
backtester (2005 -> today by default) and builds the meta-portfolio that
always holds the current champion's book.

No lookahead, by construction: the champion traded on day t is Thompson-
sampled from book values dated <= t-1, with the RNG seeded by day t's date
via allocate.pick_seed — the same date-seeded scheme `allocate.py --pick`
uses live each morning, when only yesterday's marks exist. Day t's
meta-return is then that prior-data champion's t-1 -> t book return (0.0 /
cash when the champion has no mark on either day). Picks are daily.

The report compares CAGR / Sharpe / maxDD (backtest_fleet.perf, same
formulas) for: the meta-portfolio, the best and worst single strategy in
hindsight, the daily-rebalanced equal-weight fleet, and the hold_*
buy-and-hold baselines backtest_fleet computes. hold_* books are baselines
only — never allocator candidates. Plus a champion timeline (compact
segments) and the total switch count.

Offline mode (--books PATH, hermetic — how the tests avoid the network):
JSON of per-strategy daily book values, either paper.json-style
{"books": {name: {"history": [{"date", "value"}, ...]}}} or a flat
{name: [{"date", "value"}, ...]} mapping. Default online mode reuses
backtest_fleet.build_fleet_books (yfinance); the full 20y replay re-scores
every book every day and takes on the order of a minute.

Inherited caveat: options books are Black-Scholes approximations of
synthetic contracts (no real IV surface), so this replay is directional
evidence that the allocator adds value, not truth.

Recommend-only: never touches config, order gates, or any live trading path.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import allocate
import backtest_fleet

DEFAULT_START = "2005-01-01"
META_START = 10_000.0
# first pick needs MIN_RETURNS daily returns => MIN_RETURNS + 1 marks
WARMUP_DAYS = allocate.MIN_RETURNS + 1


# --- pure replay (reused by tests) --------------------------------------------

def candidate_books(books: dict) -> dict:
    """Allocator candidates: every non-empty book except hold_* baselines."""
    return {n: b for n, b in books.items()
            if not n.startswith("hold_") and b.get("history")}


def champion_segments(picks: list[dict]) -> list[dict]:
    """Compress the daily pick list into contiguous same-champion segments."""
    segments = []
    for p in picks:
        if segments and segments[-1]["champion"] == p["champion"]:
            segments[-1]["end"] = p["date"]
            segments[-1]["days"] += 1
        else:
            segments.append({"champion": p["champion"], "start": p["date"],
                             "end": p["date"], "days": 1})
    return segments


def replay(books: dict, half_life: float = allocate.HALF_LIFE_DAYS,
           seed: int = 0, warmup: int = WARMUP_DAYS,
           start: str | None = None, end: str | None = None) -> dict:
    """Day-by-day allocator replay over per-strategy daily book values.

    The trading calendar is the sorted union of candidate book dates inside
    [start, end]. For each day t after the first `warmup` calendar days, the
    champion is allocate.thompson_pick over book histories truncated to
    dates <= t-1 (entries before --start still inform scores — they are in
    the past, so that is not lookahead), RNG-keyed by day t's date and
    `seed`. The meta-portfolio compounds the champion's t-1 -> t return;
    the equal-weight curve compounds the mean return of every candidate
    with marks on both days (daily rebalanced).
    """
    cands = candidate_books(books)
    hists = {n: sorted(b["history"], key=lambda h: h["date"])
             for n, b in cands.items()}
    calendar = sorted({h["date"] for hist in hists.values() for h in hist
                       if (start is None or h["date"] >= start)
                       and (end is None or h["date"] <= end)})
    if len(calendar) <= warmup:
        raise ValueError(f"need more than --warmup ({warmup}) days of book "
                         f"history in the window, got {len(calendar)}")
    vals = {n: {h["date"]: h["value"] for h in hist}
            for n, hist in hists.items()}

    ptr = dict.fromkeys(hists, 0)
    meta_val = ew_val = META_START
    meta_hist = [{"date": calendar[warmup - 1], "value": META_START}]
    ew_hist = [{"date": calendar[warmup - 1], "value": META_START}]
    picks = []
    for i in range(warmup, len(calendar)):
        day, prev = calendar[i], calendar[i - 1]
        for n, hist in hists.items():
            p = ptr[n]
            while p < len(hist) and hist[p]["date"] <= prev:
                p += 1
            ptr[n] = p
        trunc = {n: {"history": hists[n][:ptr[n]]} for n in hists}
        champ = allocate.thompson_pick(trunc, day, seed=seed,
                                       half_life=half_life)["champion"]
        picks.append({"date": day, "champion": champ})

        v_prev, v_now = vals[champ].get(prev), vals[champ].get(day)
        ret = v_now / v_prev - 1.0 if v_prev and v_now is not None else 0.0
        meta_val *= 1.0 + ret
        meta_hist.append({"date": day, "value": meta_val})

        rets = []
        for n in sorted(vals):
            a, b = vals[n].get(prev), vals[n].get(day)
            if a and b is not None:
                rets.append(b / a - 1.0)
        ew_val *= 1.0 + (sum(rets) / len(rets) if rets else 0.0)
        ew_hist.append({"date": day, "value": ew_val})

    segments = champion_segments(picks)
    return {"start": calendar[warmup], "end": calendar[-1], "warmup": warmup,
            "half_life": half_life, "seed": seed, "picks": picks,
            "segments": segments,
            "switches": max(len(segments) - 1, 0),
            "meta_history": meta_hist, "ew_history": ew_hist}


# --- reporting -----------------------------------------------------------------

def slim_perf(history: list[dict]) -> dict:
    """CAGR / Sharpe / maxDD via backtest_fleet.perf (same formulas)."""
    r = backtest_fleet.perf({"history": history, "trades": []})
    return {"years": r["years"], "cagr": round(float(r["cagr"]), 6),
            "sharpe": round(float(r["sharpe"]), 6),
            "max_dd": round(float(r["max_dd"]), 6),
            "final": round(float(r["final"]), 2)}


def comparison_rows(books: dict, result: dict) -> dict:
    """Stats rows over the replay window: meta, best/worst single strategy
    in hindsight, equal-weight fleet, and the hold_* baselines."""
    lo = result["meta_history"][0]["date"]
    hi = result["meta_history"][-1]["date"]

    def window(book):
        return [h for h in sorted(book.get("history", []),
                                  key=lambda h: h["date"])
                if lo <= h["date"] <= hi]

    rows = {"meta": slim_perf(result["meta_history"])}
    windowed = {n: wh for n, wh in
                ((n, window(b)) for n, b in candidate_books(books).items())
                if len(wh) >= 2}
    if windowed:
        def ratio(n):
            return windowed[n][-1]["value"] / windowed[n][0]["value"]
        best = max(sorted(windowed), key=ratio)
        worst = min(sorted(windowed), key=ratio)
        rows[f"best_single ({best})"] = slim_perf(windowed[best])
        rows[f"worst_single ({worst})"] = slim_perf(windowed[worst])
    rows["equal_weight"] = slim_perf(result["ew_history"])
    for n in sorted(books):
        if n.startswith("hold_"):
            wh = window(books[n])
            if len(wh) >= 2:
                rows[n] = slim_perf(wh)
    return rows


CAVEAT = ("options books are Black-Scholes approximations — "
          "directional evidence, not truth")


def print_report(result: dict, rows: dict, args) -> None:
    if args.json:
        champion_days = {}
        for p in result["picks"]:
            champion_days[p["champion"]] = champion_days.get(p["champion"], 0) + 1
        print(json.dumps({
            "mode": "books" if args.books else "online",
            "cadence": "daily",
            "start": result["start"], "end": result["end"],
            "warmup": result["warmup"], "half_life": args.half_life,
            "seed": args.seed, "picks": len(result["picks"]),
            "switches": result["switches"],
            "champion_days": {n: champion_days[n]
                              for n in sorted(champion_days)},
            "rows": rows, "segments": result["segments"],
            "caveat": CAVEAT}, indent=2))
        return
    print(f"allocator replay {result['start']} -> {result['end']} "
          f"(daily picks, half-life {args.half_life:g}d, seed {args.seed}, "
          f"warmup {result['warmup']}d)")
    print(CAVEAT)
    print(f"champion switches: {result['switches']} over "
          f"{len(result['picks'])} picks\n")
    header = (f"{'portfolio':<32} {'years':>5} {'CAGR':>8} {'sharpe':>6} "
              f"{'maxDD':>8} {'final':>12}")
    print(header)
    print("-" * len(header))
    for label, r in rows.items():
        print(f"{label:<32} {r['years']:>5} {r['cagr']:>8.2%} "
              f"{r['sharpe']:>6.2f} {r['max_dd']:>8.2%} {r['final']:>12,.0f}")
    print("\nchampion timeline:")
    for seg in result["segments"]:
        print(f"  {seg['start']} -> {seg['end']}  "
              f"{seg['champion']:<24} {seg['days']:>5}d")


# --- offline books loading -------------------------------------------------------

def load_books(path: Path) -> dict:
    """Load per-strategy daily book values from JSON: paper.json-style
    {"books": {name: {"history": [...]}}} or flat {name: [history...]}."""
    data = json.loads(path.read_text())
    raw = data.get("books", data) if isinstance(data, dict) else None
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path}: books file must be a non-empty JSON "
                         f"object of per-strategy book values")
    books = {}
    for name, b in raw.items():
        hist = b.get("history", []) if isinstance(b, dict) else b
        if not isinstance(hist, list):
            raise ValueError(f"{path}: book {name!r} has no history list")
        books[name] = {"history": hist,
                       "trades": b.get("trades", [])
                       if isinstance(b, dict) else []}
    return books


# --- CLI ---------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--books", default=None, metavar="PATH",
                    help="offline mode: JSON of per-strategy daily book "
                         "values (no network)")
    ap.add_argument("--start", default=None, metavar="YYYY-MM-DD",
                    help=f"replay window start (online default {DEFAULT_START})")
    ap.add_argument("--end", default=None, metavar="YYYY-MM-DD")
    ap.add_argument("--half-life", type=float, default=allocate.HALF_LIFE_DAYS,
                    help="decay half-life passed to the allocator "
                         f"(default {allocate.HALF_LIFE_DAYS})")
    ap.add_argument("--seed", type=int, default=0,
                    help="seed offset folded into the date-seeded RNG scheme")
    ap.add_argument("--warmup", type=int, default=WARMUP_DAYS,
                    help="calendar days observed before the first pick "
                         f"(default {WARMUP_DAYS})")
    ap.add_argument("--iv-premium", type=float, default=1.15,
                    help="online mode only: passed to backtest_fleet")
    ap.add_argument("--opt-slip-pct", type=float, default=0.015,
                    help="online mode only: passed to backtest_fleet")
    ap.add_argument("--rate", type=float, default=0.04,
                    help="online mode only: passed to backtest_fleet")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.half_life <= 0:
        ap.error(f"--half-life must be > 0, got {args.half_life:g}")
    if args.warmup < 1:
        ap.error(f"--warmup must be >= 1, got {args.warmup}")
    for flag, value in (("--start", args.start), ("--end", args.end)):
        if value is None:
            continue
        try:
            if len(value) != 10:
                raise ValueError(value)
            dt.datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            ap.error(f"{flag} must be YYYY-MM-DD, got {value!r}")

    if args.books:
        try:
            books = load_books(Path(args.books))
        except FileNotFoundError:
            print(f"no books file at {args.books}", file=sys.stderr)
            sys.exit(1)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"unreadable books file: {exc}", file=sys.stderr)
            sys.exit(1)
    else:
        import pandas as pd
        start = args.start or DEFAULT_START
        books = backtest_fleet.build_fleet_books(
            pd.Timestamp(start), args.end, args.iv_premium,
            args.opt_slip_pct, args.rate)

    try:
        result = replay(books, half_life=args.half_life, seed=args.seed,
                        warmup=args.warmup, start=args.start, end=args.end)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    print_report(result, comparison_rows(books, result), args)


if __name__ == "__main__":
    main()
