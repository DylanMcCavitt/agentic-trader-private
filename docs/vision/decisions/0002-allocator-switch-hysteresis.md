# ADR 0002 — Allocator switch hysteresis: HYSTERESIS = 3.0 (2026-06-12)

- **Status:** Accepted
- **Date:** 2026-06-12
- **Issue:** #41
- **Context:** the 2026-06-12 replay ([ADR 0001](0001-allocator-replay-findings.md))
  showed excessive champion switching in the recommend-only allocator. This
  records the decision to damp it. Moved verbatim out of `docs/strategies.md`.

## Decision

Decision, per issue #41: damp the switching via **switch hysteresis**,
tuned by replay. The 63d half-life and the diffuse prior stay unchanged.

Mechanism: the daily pick is now incumbent-aware. Yesterday's champion
stays champion unless a challenger's Thompson draw exceeds the
incumbent's draw by `h * incumbent_posterior_std` — the margin is
expressed in units of the incumbent's posterior std because draws live
on a ~1e-3 daily-return scale, where a raw constant would be
meaningless. One module constant (`allocate.HYSTERESIS`, overridable
with `--hysteresis` on both `allocate.py --pick` and
`replay_allocator.py`); `h = 0` reproduces the old highest-draw
behavior. Insufficient-data incumbents get no protection, draws /
weights / determinism / the date-seeded scheme are unchanged, and the
incumbent is by construction prior information (picked on data through
*t−2*), so the no-lookahead rule is untouched.

Sweep, run 2026-06-12: fleet books were built online once and
snapshotted, then every `h` was replayed over that identical snapshot
(eliminating the yfinance download variance noted above). Window after
warmup 2005-02-02 → 2026-06-12, 5,374 daily picks, seed 0:

```text
   h  switches     CAGR  sharpe    maxDD    final  champions  mean reign
   0      3448  -10.39%   -0.01  -97.15%      960         10        1.6d
 0.5      2594   -8.54%    0.06  -97.67%    1,486         10        2.1d
   1      1678   -6.24%    0.12  -96.73%    2,526         10        3.2d
   2       510   -8.12%    0.10  -98.17%    1,638         10       10.5d
   3       107   -3.77%    0.19  -96.00%    4,405         10       49.8d
   4        25   -0.57%    0.27  -95.42%    8,856          8      206.7d
   5        15    5.04%    0.37  -90.85%   28,567          9      335.9d
```

(equal_weight on this snapshot: +3.46% CAGR, 0.36 Sharpe, −20.66%
maxDD — identical across `h` by construction.)

Robustness checks before choosing a default:

- **Seed sensitivity** (`--seed 1`, same snapshot): h=0 → 3,473
  switches, −3.59% CAGR; h=1 → 1,700, +0.61%; h=3 → 94, +3.88%. The
  CAGR *level* moves by ~7 points between seeds — more than most
  adjacent-`h` differences — so the CAGR column above is
  noise-dominated and must not be cherry-picked. What is stable across
  seeds: the switch counts (within ~1%) and the directional
  CAGR/Sharpe improvement as `h` rises.
- **Grid edge**: h=4 and h=5 degenerate toward never-switching — mean
  reigns of 207d and 336d, single reigns up to 2,365 days (~9 years),
  i.e. the "allocator" collapses into buy-and-hold of one book. Their
  better stats are just riding one long lucky reign, not allocation.

Chosen default: **`HYSTERESIS = 3.0`**. Rationale: since the CAGR point
estimates are seed noise, the load-bearing criterion is the switching
timescale. At h=3 the mean champion reign (~50–57d across seeds)
finally matches the 63d evidence half-life — the champion changes at
roughly the rate the evidence itself can change — while h=1–2 still
churn far faster than evidence (3–10d reigns) and h≥4 outlives the
evidence window several times over. h=3 also happens to have the best
CAGR/Sharpe/maxDD in *both* seeds among non-degenerate values, and all
10 strategies still take the champion seat (longest reign 484d).
Overfitting caveat: h=3 sits at the edge of the original 0–3 grid and
was picked on the same history it will be judged on; the extension to
h=4/5 and the seed check bound that risk but do not remove it.

Before/after at the chosen default, identical data snapshot (the
recorded h=0 baseline in the section above used a marginally different
yfinance download — same directional picture):

```text
                    switches   switches/yr     CAGR  sharpe    maxDD
h=0 (old behavior)      3448          ~161  -10.39%   -0.01  -97.15%
h=3 (new default)        107            ~5   -3.77%    0.19  -96.00%
```

Honest conclusion: hysteresis does what it was asked to do — switching
drops by ~97% and CAGR/Sharpe improve directionally in both seeds —
but it does **not** make the allocator add value. At every
non-degenerate `h`, the meta-portfolio still fails to robustly beat
the equal-weight fleet (the sign of meta-vs-equal-weight flips with
the RNG seed at h=3) and the max drawdown stays catastrophic
(−82% … −98%). Hysteresis treats the symptom (churn), not the cause:
on any given day the pick still allocates 100% to a single, often
highly volatile options book. Do not trust the picks for promotion;
the remaining levers (tighter prior, slower cadence, or champion
diversification) are future work — recommendation only, no other
constants changed here.

Same inherited caveat as above: options books are Black-Scholes
approximations of synthetic contracts, so every number in this section
is directional evidence, not truth.
