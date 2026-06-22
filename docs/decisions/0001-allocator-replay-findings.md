# ADR 0001 — Allocator replay findings (2026-06-12)

- **Status:** Recorded (informational findings; no constants changed)
- **Date:** 2026-06-12
- **Context:** historical evaluation of the recommend-only Thompson allocator
  (`scripts/allocate.py` / `scripts/replay_allocator.py`) before trusting its
  picks. Moved verbatim out of `docs/strategies.md` so that file stays a living
  reference and these dated findings survive as an immutable record.

## Findings

Run on 2026-06-12 with default settings — daily picks, half-life 63d,
seed 0 (the live date-seeded `allocate.pick_seed` scheme), warmup 21d:

```bash
uv run scripts/replay_allocator.py          # human-readable table
uv run scripts/replay_allocator.py --json   # precise numbers (recorded below)
```

Replay window after warmup: 2005-02-02 → 2026-06-12, 5374 daily picks.
Numbers below are from the `--json` run. Note: the replay itself is
deterministic for identical inputs, but two back-to-back online runs
produced slightly different tables (e.g. 3448 vs 3559 switches) because
yfinance served marginally different adjusted history per download; the
directional picture was identical in both.

```text
portfolio                             years     CAGR  sharpe    maxDD    final
------------------------------------------------------------------------------
meta                                   21.4  -13.21%   -0.16  -98.59%      485
best_single (opt_rsi2_call_qqq)        21.4   12.90%    0.53  -46.29%  114,961
worst_single (opt_breakout_call_spy)   21.4  -16.40%   -0.23  -97.88%      218
equal_weight                           21.4    2.14%    0.26  -24.09%   15,713
hold_iwm                               21.4    8.93%    0.48  -58.64%   60,779
hold_qqq                               21.4   15.71%    0.78  -53.40%  214,304
hold_spy                               21.4   10.96%    0.64  -55.19%   91,124
```

Champion timeline: 3559 switches over 5374 picks (~166 switches/year),
10 distinct champions, 3560 segments with mean reign ~1.5 days — 2602
segments lasted a single day; the longest reign was 29 days
(opt_rsi2_call_qqq, 2025-01-23 → 2025-03-05). Champion-day totals:
opt_rsi2_call_qqq 1537, opt_ibs_call_iwm 780, opt_breakout_call_spy 657,
opt_rsi2_put_spy 635, momentum_rotation 592, donchian_qqq 410,
opt_breakdown_put_qqq 383, ibs_qqq 205, bollinger_spy 110, rsi2_spy 65.

Read: champion switching is excessive. The meta-portfolio switches on
~66% of trading days, underperforms the daily-rebalanced equal-weight
fleet by ~15 CAGR points (-13.21% vs +2.14%), trails the
best-in-hindsight strategy by ~26 points, and lands near the worst
single book with a -98.6% drawdown — the picks behave closer to a daily
random draw across volatile option books than to a tracker of the
leading strategy. With a 63d half-life supplying real evidence, that
pattern says the diffuse prior / Thompson exploration variance is
overwhelming the decayed evidence at a daily re-pick cadence, not that
the half-life itself is wrong. Recommendation: do not trust the current
picks as-is; before any promotion, investigate damping the switching
(e.g. a tighter prior, switch hysteresis, or a slower pick cadence) —
recommendation only, no constants changed here.

Same inherited caveat as above: options books are Black-Scholes
approximations, so this is directional evidence, not truth.

## Follow-up

Superseded in part by [ADR 0002](0002-allocator-switch-hysteresis.md), which
adopted switch hysteresis (`HYSTERESIS = 3.0`) to damp the churn identified here.
