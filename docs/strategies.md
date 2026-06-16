# The strategy fleet

> Disclaimer: educational/reference software, not financial or investment
> advice.

Beyond the single live strategy (see [strategy.md](strategy.md)), the harness
forward-tests a fleet of 10 strategies — 5 equity, 5 single-leg options — in
parallel paper books, so dry-run time is spent comparing candidates instead
of watching one. Each daily run (`scripts/run_strategies.py`, step 8 of
[TRADER.md](../TRADER.md)) evaluates every enabled strategy, simulates fills
into its own $10k paper book in `state/paper.json`, and logs to
`logs/paper.md`. No real orders are ever placed by the fleet.

Compare books at any time:

```bash
uv run scripts/scoreboard.py
```

## Design

One signal contract drives everything (`scripts/strategies/signals.py`):

```
signal(daily_ohlc_df, params) -> {entry, exit, reason, metrics}
```

The engine maps `entry/exit` to BUY/SELL for equity books and OPEN/CLOSE for
option books, so the same signal can power both an equity strategy and its
leveraged options expression. Options strategies buy single-leg contracts
only — deep ITM, defined DTE window — matching what the Robinhood MCP
supports (Level 2; no spreads) and what `scripts/option_gate.py` would allow
live. All ten are long-premium/long-equity: max loss is the book.

Option contracts are picked and marked via yfinance chains (~15-minute
delayed quotes — fine for paper). Fills charge `slippage_bps` on equities
and `option_spread_take` × half-spread on options; an expired option that
was never closed settles at intrinsic value.

## Equity strategies

| name | symbol | idea | entry | exit |
|---|---|---|---|---|
| `rsi2_spy` | SPY | Connors RSI(2) mean reversion (the live strategy) | close > SMA200 and RSI(2) < 10 | close > SMA5 |
| `ibs_qqq` | QQQ | Internal Bar Strength mean reversion | IBS < 0.2 and close > SMA200 | IBS > 0.8 or close > SMA5 |
| `bollinger_spy` | SPY | Bollinger-band dip buying | close < lower band (20d, 2σ) and close > SMA200 | close ≥ middle band |
| `donchian_qqq` | QQQ | Donchian channel breakout (trend following) | close > prior 20d high | close < prior 10d low |
| `momentum_rotation` | SPY/QQQ | Relative momentum rotation | hold the leader by 126d return while positive | leader change or negative → rotate/cash |

## Options strategies (single-leg, long-only)

| name | underlying | idea | entry | exit |
|---|---|---|---|---|
| `opt_rsi2_call_qqq` | QQQ | RSI(2) dip → deep ITM call (~5% ITM, 21–45 DTE) | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |
| `opt_breakout_call_spy` | SPY | breakout → slightly ITM call (2% ITM, 30–60 DTE) | close > prior 20d high | close < prior 10d low, or ≤ 10 DTE |
| `opt_rsi2_put_spy` | SPY | overbought rally in a downtrend → ITM put (5% ITM, 21–45 DTE) | close < SMA200 and RSI(2) > 90 | RSI(2) < 30, or ≤ 7 DTE |
| `opt_ibs_call_iwm` | IWM | IBS dip → ITM call (5% ITM, 21–45 DTE) | IBS < 0.15 and close > SMA200 | IBS > 0.8 or close > SMA5, or ≤ 7 DTE |
| `opt_breakdown_put_qqq` | QQQ | breakdown in a downtrend → ITM put (2% ITM, 30–60 DTE) | close < prior 20d low and close < SMA200 | close > prior 10d high, or ≤ 10 DTE |
| `opt_rsi2_call_aapl` | AAPL | RSI(2) dip → ATM call (28–45 DTE) — option-sleeve candidate | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |
| `opt_rsi2_call_msft` | MSFT | RSI(2) dip → ATM call (28–45 DTE) — option-sleeve candidate | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |
| `opt_rsi2_call_nvda` | NVDA | RSI(2) dip → ATM call (28–45 DTE) — option-sleeve candidate | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |
| `opt_rsi2_call_googl` | GOOGL | RSI(2) dip → ATM call (28–45 DTE) — option-sleeve candidate | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |
| `opt_rsi2_call_amzn` | AMZN | RSI(2) dip → ATM call (28–45 DTE) — option-sleeve candidate | close > SMA200 and RSI(2) < 10 | close > SMA5, or ≤ 7 DTE |

Why deep ITM and short DTE windows: ITM minimizes theta bleed and IV-crush
drag, so the position behaves like leveraged delta on the underlying signal
rather than a volatility bet; the DTE stop (`exit_dte`) forces an exit
before gamma/theta get violent into expiry.

Mean-reversion entries (RSI(2)/IBS) resolve in 1–5 days, so they pay very
little theta. The honest caveat: IV is usually elevated exactly when those
entries fire, which is why the calls are bought deep in the money.

The five liquid-cap calls (`opt_rsi2_call_{aapl,msft,nvda,googl,amzn}`) are
the **option sleeve** candidate set (`option_sleeve.candidates`). They paper-
trade like the others, but the live sleeve selects a *budget-affordable*
contract (not necessarily ITM — see the option sleeve section below), since
one mega-cap contract must fit under the live `max_option_premium_usd` cap.

## Backtesting the fleet

```bash
uv run scripts/backtest_fleet.py                  # 2005 -> today
uv run scripts/backtest_fleet.py --start 2015-01-01
```

Replays the exact vectorized signals through the same fill math as the
paper engine (`scripts/paper.py`), one $10k book per strategy, with
buy-and-hold rows for context. Equity rows are as trustworthy as
`scripts/backtest.py`; **options rows are an approximation** — synthetic
contracts at the configured moneyness/DTE, Black-Scholes priced from 21d
EWMA realized vol × `--iv-premium` (default 1.15), `--opt-slip-pct`
(default 1.5%) per side. No real IV surface means vol-crush after
mean-reversion entries is underestimated, so treat options results as
optimistic direction, not truth. Chain-accurate options backtesting needs
historical chain data (e.g. QuantConnect/LEAN).

Indicators warm up on data before `--start`; trading begins at `--start`.

## Allocator (recommend-only)

`scripts/allocate.py` ranks the paper books by exponentially decayed Sharpe
of daily book returns, and `--pick` Thompson-samples a daily champion from
the same books. The daily run (step 11 of [TRADER.md](../TRADER.md)) records
the verdict:

```bash
uv run scripts/allocate.py --pick --record
```

Each verdict (date, champion, per-strategy weights and scores) is appended
to untracked `state/allocator.json` — idempotent per day; `--force`
re-evaluates and replaces that day's entry — so the allocator builds a
visible track record. `scripts/scoreboard.py` surfaces the current champion
and the recent pick history with switch markers.

The allocator is strictly recommend-only: its verdict changes no config and
is never read by the order gates (`scripts/order_gate.py`,
`scripts/option_gate.py`). Promoting a champion to live trading is a
deliberate human config change via the promotion path below — never an
automatic consequence of a pick.

## Promotion path

1. Let the fleet run dry for several weeks; watch `scripts/scoreboard.py`.
2. Equity candidate: point the live config (`symbol` + decide.py params) at
   it; the existing equity gate applies unchanged.
3. Options candidate: the **option sleeve** already runs an options strategy
   live (dry-run, gated) — see below. To change which one, edit
   `option_sleeve.candidates` / `default` in `config.json`; the sleeve picks
   the best-scoring candidate once it has paper history. The gate's known
   limit: the order payload carries only the option instrument UUID, so the
   underlying can't be verified deterministically; the premium cap and the
   dedicated account are the backstops.

## Option sleeve (live, gated)

A second dry-run sleeve, alongside the equity sleeve, trades a single long
option per day in the dedicated account (steps 8–9 of
[TRADER.md](../TRADER.md)). It mirrors the equity sleeve's "go straight to
dry-run" model — it never waits on paper performance to start trading.

- **Selection** (`scripts/select_sleeve.py`): among `option_sleeve.candidates`
  it picks the best by decayed Sharpe once a candidate has ≥
  `option_sleeve.min_score_days` paper returns; until then it uses
  `option_sleeve.default`. It *always* returns a strategy — the 20-return
  threshold gates paper ranking only, never whether the sleeve trades.
- **Decision** (`scripts/decide_option.py`): the chosen strategy's signal →
  OPEN / CLOSE / HOLD / NONE, with a `≤ exit_dte` near-expiry close override.
- **Contract** (`scripts/select_option_contract.py`): from the broker's chain,
  the nearest expiry in the DTE window, then the highest-premium contract whose
  1-lot cost still fits under `max_option_premium_usd` — the most meaningful
  affordable contract, **not** necessarily in the money.
- **Gate** (`scripts/option_gate.py`): long-only, limit-only opens, premium and
  contract caps, one option order per day (its own marker, separate from the
  equity gate), dry-run/halt/account/market-hours, fail-closed.
- **Reconcile**: `scripts/reconcile_state.py --kind option` writes
  `last_option_action` from the broker order list.

Default candidate set: liquid large-cap calls (AAPL, MSFT, NVDA, GOOGL, AMZN).
Because one mega-cap contract can exceed a tight premium cap, the sleeve buys
the affordable strike rather than a fixed moneyness; raise
`max_option_premium_usd` to let pricier 1-lots through the placement step.

## Adding or tuning strategies

Each entry in `config.json` `strategies` is `{enabled, kind, symbol(s),
signal, right?, params}`. Disable one by setting `enabled: false` (its book
stays in `state/paper.json` but stops updating). Add one by combining an
existing signal with new params, or write a new signal function and register
it in `scripts/strategies/__init__.py`. Books are keyed by strategy name —
rename means a fresh book.

## Replaying the allocator over history

Slice 4 of the allocator: evidence that the Thompson champion scheme adds
value before anyone trusts its picks.

```bash
uv run scripts/replay_allocator.py                     # 2005 -> today (network)
uv run scripts/replay_allocator.py --books books.json  # offline, hermetic
```

`scripts/replay_allocator.py` rebuilds each strategy's daily book values
with the fleet backtester (`backtest_fleet.build_fleet_books`), then
replays `scripts/allocate.py --pick` day by day. The convention, chosen so
there is no lookahead: the champion traded on day *t* is Thompson-sampled
from book values dated *t−1* and earlier, with the RNG keyed by day *t*'s
date (and `--seed`) via `allocate.pick_seed` — exactly the date-seeded
scheme the live `--pick` runs each morning, when only yesterday's marks
exist. Day *t*'s meta-return is then that prior-data champion's
*t−1 → t* book return; the meta-portfolio always holds the current
champion's book (cash for a day the champion has no mark). Picks are
daily.

The report compares CAGR / Sharpe / maxDD (same `perf` formulas as
`backtest_fleet.py`) for the meta-portfolio against the best and worst
single strategy in hindsight, the daily-rebalanced equal-weight fleet, and
the `hold_*` buy-and-hold baselines — baselines are never allocator
candidates — plus a champion timeline of compact segments and the total
switch count. `--start`/`--end` bound the replay window (book history
before `--start` still informs scores: it is in the past, not lookahead),
`--half-life` and `--seed` pass through to the allocator, `--warmup`
(default 21 trading days, enough for the first pick to be scoreable)
delays the first pick, and `--json` emits the machine-readable report —
byte-identical for the same inputs.

`--books PATH` replays a JSON file of per-strategy daily book values
instead of hitting the network — either paper.json-style
`{"books": {name: {"history": [{"date", "value"}, ...]}}}` or a flat
`{name: [{"date", "value"}, ...]}` mapping. That is how the tests stay
hermetic, including the no-lookahead test that injects a one-day +50%
spike into a book and proves the meta-portfolio cannot capture it.

Inherited caveat: the options books come from Black-Scholes-priced
synthetic contracts (see "Backtesting the fleet" above), so the replay is
directional evidence that the allocator adds value, not truth.

### Replay results (2005 → 2026-06-12)

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

### Switch hysteresis (decision 2026-06-12)

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
