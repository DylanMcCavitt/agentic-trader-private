#!/usr/bin/env python3
"""Pick which option strategy the live (dry-run) option sleeve trades today.

Usage: uv run scripts/select_sleeve.py

The option sleeve mirrors the equity sleeve's "go straight to dry-run" model:
it ALWAYS returns a tradeable option strategy and never blocks on a paper-
history threshold. Among the configured ``option_sleeve.candidates`` it picks
the best one by the allocator's decayed Sharpe -- but only once a candidate
has at least ``option_sleeve.min_score_days`` daily paper returns. Until then
(cold start, and the normal state for a fresh book) it falls back to
``option_sleeve.default``.

``min_score_days`` governs WHICH candidate wins, never WHETHER the sleeve
trades. The 20-daily-returns guardrail (allocate.MIN_RETURNS) stays in
allocate.py for ranking the paper fleet only -- it is deliberately not a gate
on going live-dry, exactly as the equity sleeve was never gated on it.

Recommend-only plumbing: this reads paper books and config and prints a JSON
verdict. It places no orders and writes no state.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import allocate
from order_gate import deep_merge

ROOT = Path(__file__).parent.parent
PAPER_PATH = ROOT / "state" / "paper.json"


def load_config(root: Path = ROOT) -> dict:
    """Tracked config.json deep-merged with untracked config.local.json."""
    cfg = json.loads((root / "config.json").read_text())
    local = root / "config.local.json"
    if local.exists():
        cfg = deep_merge(cfg, json.loads(local.read_text()))
    return cfg


def load_books(paper_path: Path = PAPER_PATH) -> dict:
    """Paper books from state/paper.json. A missing or unreadable file is an
    empty fleet -- the sleeve still trades its default, never errors out."""
    if not paper_path.exists():
        return {}
    try:
        state = json.loads(paper_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    books = state.get("books") if isinstance(state, dict) else None
    return books if isinstance(books, dict) else {}


def candidate_specs(cfg: dict) -> list[tuple[str, dict]]:
    """The option_sleeve candidates that are real, enabled option strategies,
    preserving configured order."""
    sleeve = cfg.get("option_sleeve") or {}
    strategies = cfg.get("strategies") or {}
    out = []
    for name in sleeve.get("candidates", []):
        spec = strategies.get(name)
        if (isinstance(spec, dict) and spec.get("enabled")
                and spec.get("kind") == "option"):
            out.append((name, spec))
    return out


def select(cfg: dict, books: dict, min_score_days: int) -> dict:
    """Choose today's option-sleeve strategy. Always returns a strategy when
    at least one valid candidate exists; basis explains the choice."""
    cands = candidate_specs(cfg)
    if not cands:
        return {"strategy": None, "basis": "none",
                "reason": "no enabled option_sleeve candidates in config"}

    specs = dict(cands)
    names = [n for n, _ in cands]
    sleeve = cfg.get("option_sleeve") or {}
    default = sleeve.get("default")

    scored = []
    for name in names:
        s = allocate.score_book(books.get(name, {}), min_returns=min_score_days)
        scored.append({"strategy": name, "score": s["score"],
                       "days": s["days"], "insufficient": s["insufficient"]})

    eligible = [r for r in scored if not r["insufficient"]]
    if eligible:
        eligible.sort(key=lambda r: (-r["score"], r["strategy"]))
        chosen = eligible[0]["strategy"]
        basis = "best_score"
        reason = (f"best decayed-Sharpe of {len(eligible)} scored candidate(s): "
                  f"{chosen} (score {eligible[0]['score']:.2f}, "
                  f"{eligible[0]['days']}d)")
    else:
        if default in specs:
            chosen, basis = default, "default"
        else:
            chosen, basis = names[0], "default_fallback"
        max_days = max((r["days"] for r in scored), default=0)
        reason = (f"cold start: no candidate has >= {min_score_days} paper "
                  f"returns yet (max {max_days}d) -- using "
                  f"{'configured default' if basis == 'default' else 'first candidate'} "
                  f"{chosen}")

    spec = specs[chosen]
    row = next(r for r in scored if r["strategy"] == chosen)
    return {"strategy": chosen, "symbol": spec["symbol"], "right": spec["right"],
            "basis": basis, "min_score_days": min_score_days,
            "score": row["score"], "days": row["days"],
            "candidates": scored, "reason": reason}


def main() -> None:
    argparse.ArgumentParser(description=__doc__).parse_args()
    cfg = load_config()
    sleeve = cfg.get("option_sleeve") or {}
    if not sleeve.get("enabled"):
        print(json.dumps({"strategy": None, "basis": "disabled",
                          "reason": "option_sleeve.enabled is false"}))
        return
    min_score_days = sleeve.get("min_score_days", allocate.MIN_RETURNS)
    print(json.dumps(select(cfg, load_books(), min_score_days)))


if __name__ == "__main__":
    main()
