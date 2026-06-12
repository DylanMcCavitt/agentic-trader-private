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

Why deep ITM and short DTE windows: ITM minimizes theta bleed and IV-crush
drag, so the position behaves like leveraged delta on the underlying signal
rather than a volatility bet; the DTE stop (`exit_dte`) forces an exit
before gamma/theta get violent into expiry.

Mean-reversion entries (RSI(2)/IBS) resolve in 1–5 days, so they pay very
little theta. The honest caveat: IV is usually elevated exactly when those
entries fire, which is why the calls are bought deep in the money.

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
the same books. The daily run (step 9 of [TRADER.md](../TRADER.md)) records
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
3. Options candidate: live options orders are already gated by
   `scripts/option_gate.py` (long-only, premium-capped, 1/day, fail-closed),
   but the TRADER.md live procedure covers equities only — promoting an
   options strategy to live requires extending it deliberately. The gate's
   known limit: the order payload carries only the option instrument UUID,
   so the underlying can't be verified deterministically; the premium cap
   and the dedicated account are the backstops.

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
