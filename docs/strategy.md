# Strategy: Connors RSI(2) mean reversion (the worked example)

This is the strategy shipped behind the harness's decision interface. It is
deliberately simple and replaceable — the harness's guardrails (see the
[README](../README.md)) apply no matter what produces the signal.

## Decision interface

```
uv run scripts/decide_with_quote.py --quote-json '<broker quote JSON>' --holding <true|false>
```

The wrapper persists the broker quote to `state.last_quote` and feeds the
quote price into `scripts/decide.py`. `decide.py` can still be run directly
for tests/backtests with `--price`, but live runs should use the wrapper.

Output is a single JSON object:

```json
{
  "date": "...", "symbol": "SPY", "price": 0.0,
  "rsi2": 0.0, "sma_trend": 0.0, "sma_exit": 0.0,
  "holding": false, "decision": "BUY", "reason": "..."
}
```

`decision` is one of `BUY` / `SELL` / `HOLD` / `NONE` and is final — the
agent prompt ([TRADER.md](../TRADER.md)) forbids the model from substituting
its own market opinion. To plug in a different strategy, replace
`scripts/decide.py` with anything that honors this contract.

## Rules (long-only, SPY)

- **Entry**: close > 200-day SMA **and** RSI(2) < `entry_rsi` (default 10) →
  buy ~95% of buying power (`position_fraction`), as a dollar-sized market
  order (fractional), capped at `max_order_usd`.
- **Exit**: close > 5-day SMA → sell the full position, market order.
- **Timing**: signals are computed at ~3:45pm ET using the broker quote as a
  provisional close, so orders can fill before the 4pm close. The quote
  wrapper persists that price/timestamp for the order gate; `decide.py`
  fetches daily history through the prior session and appends the quote price
  as today's bar.

Parameters (`entry_rsi`, `sma_trend`, `sma_exit`, `slippage_bps`, sizing)
live in `config.json` and are shared between `decide.py` and `backtest.py` —
see [docs/config.md](config.md). Indicator math is identical in both scripts
(Wilder-style RSI via `ewm(alpha=1/period)`).

## Backtest

`scripts/backtest.py` runs close-to-close fills with `slippage_bps` per side
(default 2bps) and zero commission, on yfinance daily auto-adjusted data. It
sweeps SPY and QQQ across the full history, 2015+, and 2020+ windows, with
and without an optional second tranche (`scale_rsi`) and intraday stop-loss
variants. Reproduce with:

```
uv run scripts/backtest.py
```

Representative results for the configuration the live agent actually trades
(single entry, no scale-in, no stop), SPY daily data 1993–2026 (~33 years):

| Metric | Strategy | Buy & hold |
| --- | --- | --- |
| CAGR | 4.9% | 10.8% |
| Sharpe | 0.79 | — |
| Max drawdown | −14.8% | — |
| Win rate | 77% | — |
| Trades/yr | ~8 | — |
| Avg hold | 5 days | — |
| Avg trade return | +0.67% | — |
| Worst trade | −10.2% | — |
| Market exposure | 14% | 100% |

The 2020–2026 window (the scale-in variant in the sweep) shows Sharpe ≈ 1.0
with a max drawdown around −8%.

## Honest framing

This strategy does **not** beat buy-and-hold in absolute return — not over
the full history, not in any recent window, and the backtest output says so
on every line (`bh_cagr` is printed next to `cagr`). Its appeal is high
per-trade expectancy (77% win rate, +0.67% average trade) with low market
exposure (14%) and shallow drawdowns. That trade-off is the point of the
worked example, not a pitch. The stop-loss variants in the sweep generally
hurt: they cap the worst single trade but lower expectancy and, on QQQ,
deepen drawdowns.
