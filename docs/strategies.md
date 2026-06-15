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
delayed quotes — fine for paper). Fills charge `slippage_bps` on equities,
`option_spread_take` × half-spread on options, and
`option_fee_per_contract` per option open/close (defaults to $0.65 when the
paper config omits it); an expired option that was never closed settles at
intrinsic value.

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
`scripts/backtest.py`; **options rows are approximate and printed unranked** —
synthetic contracts at configured moneyness and `dte_min`,
Black-Scholes-Merton priced from 21d EWMA realized vol × `--iv-premium`
(default 1.15), dividend yields (`--div-yields`), an exit IV cap
(`--exit-iv-haircut`, default 25%), `--opt-slip-pct` (default 1.5%) per side,
and `--option-fee-per-contract` (default $0.65) per side. No real IV surface
means vol-crush/path effects are still approximate; treat option results as
directional feel, not truth. Chain-accurate options backtesting needs
historical chain data (e.g. QuantConnect/LEAN).

Indicators warm up on data before `--start`; trading begins at `--start`.

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
