# Momentum screener

Deterministic yfinance-based screens that hand the RESEARCH lane a
gate-compliant candidate list every premarket run. No LLM judgment happens
here — the screener is pure data plumbing, so its output is reproducible
and auditable.

## Universe construction

yfinance has no true screener endpoint, so the scannable universe is built
programmatically (`trader/screener/universe.py`):

1. **Index constituents** — S&P 500 + Nasdaq-100 symbols scraped from
   Wikipedia (bs4 over the constituents tables; no extra dependencies).
   When the network or page layout fails, the committed static snapshot
   `trader/screener/universe_fallback.txt` (~517 symbols) is used instead,
   so offline/CI runs are deterministic. `--offline-universe` forces the
   fallback.
2. **Curated liquid ETFs** — always included: broad index (SPY, QQQ, IWM,
   DIA), leveraged/inverse (TQQQ, SQQQ, SOXL, SOXS, UPRO, SPXU, TNA, TZA,
   …), sector, volatility, commodity/crypto/rates products. The equity
   sleeve is long-only, so the inverse ETFs in this list are how it
   expresses bearish views.

Symbols are normalized to yfinance format (`BRK.B` → `BRK-B`). The full
universe is ~560 symbols.

## Hard floors (applied to every candidate)

| Floor | Value |
|---|---|
| 20-day average daily dollar volume | ≥ $50M |
| Last price | ≥ $5 |
| US-listed | by construction (index constituents + US-listed ETFs only) |

Floors live in `trader/screener/screens.py` (`MIN_AVG_DOLLAR_VOLUME`,
`MIN_PRICE`) and are deterministic per the plan — not DB params, not
agent-tunable.

## Screens

A candidate must pass the floors **and** at least one screen:

| Screen | Rule |
|---|---|
| `movers_1d` | 1-day close-to-close change ≥ +3% |
| `movers_5d` | 5-day close-to-close change ≥ +8% |
| `volume_surge` | today's volume ≥ 2× the 20-day average (average excludes today) |
| `gap_up` | today's open ≥ +2% above prior close |

All math runs on daily OHLCV bars batch-downloaded via `yf.download`
(batches of 100 tickers, 3 months of history). Symbols that fail to
download are dropped with a warning; the run only fails (nonzero exit)
when *nothing* downloads.

## CLI

```sh
trader screener run [--top N] [--record] [--offline-universe]
trader screener check SYMBOL
trader quotes snapshot SYMBOL...
```

`screener run` prints a JSON report to stdout:

```json
{
  "generated_at": "...",
  "universe": {"size": 560, "source": "wikipedia|fallback"},
  "fetched": 552,
  "floors": {"min_avg_dollar_volume": 50000000, "min_price": 5.0},
  "warnings": ["8/560 symbols had no data"],
  "candidates": [
    {
      "symbol": "TQQQ", "last": 106.0,
      "pct_chg_1d": 6.0, "pct_chg_5d": 6.0,
      "dollar_volume": 200000000.0, "volume_ratio": 1.0, "gap_pct": 0.0,
      "screens": ["movers_1d"],
      "floors": {"min_price": true, "min_avg_dollar_volume": true},
      "passes_floors": true
    }
  ]
}
```

Candidates are sorted by 1-day % change descending (symbol as tiebreak).
`--record` additionally writes the report as a `lane_runs` artifact
(lane `screener`). `screener check` returns the same per-symbol shape with
an `ok` boolean and exits nonzero on failure — the RISK lane or gates can
shell out to it to re-verify a symbol against the floors.

`quotes snapshot` fetches current price/liquidity via yfinance and writes
`quotes` rows (price, bid/ask best-effort, 20d avg dollar volume, share
volume in the payload), which the gates use for quote-freshness checks and
the digest uses to mark open positions to market.

## How RESEARCH consumes it

The RESEARCH lane runs `trader screener run --top 20` premarket and treats
the JSON as its raw candidate pool: every symbol in it already clears the
liquidity/price floors the gates will re-enforce at order time, so any
thesis built on a screener candidate cannot fail those gate checks on
liquidity grounds. The lane layers news/context judgment on top and writes
its brief for THESIS; it must not invent symbols outside the screener
output unless it re-validates them with `trader screener check`.
