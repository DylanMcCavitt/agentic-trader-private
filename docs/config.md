# Configuration reference

All runtime configuration lives in `config.json` at the repo root.
`config.example.json` mirrors it key-for-key with safe defaults — copy it to
get started:

```bash
cp config.example.json config.json
```

The example is strict JSON (no comments); this file is the commentary.

## Who reads what

- `scripts/order_gate.py` (the PreToolUse order gate) and the trading run
  (`TRADER.md` via `scripts/load_config.py`) read `config.json` deep-merged
  with the untracked `config.local.json` (see
  [Local overrides](#local-overrides-configlocaljson)).
- `scripts/decide.py` and `scripts/backtest.py` read the tracked
  `config.json` directly — local overrides do **not** affect the strategy
  math.

## Knobs

### `symbol`

- **Purpose**: the one and only ticker the system trades.
  `scripts/decide.py` fetches its daily history to compute the signal, and
  the order gate hard-blocks any order for a different symbol.
- **Units**: ticker string.
- **Safe range**: a single highly liquid, fractional-tradable US equity/ETF.
  The strategy was designed and backtested on broad-market ETFs (SPY, QQQ);
  changing it without re-running `scripts/backtest.py` is not advised.
- **Default**: `"SPY"`.

### `account_number`

- **Purpose**: the Robinhood account the system is allowed to trade. The
  order gate blocks any order whose account does not match it.
- **Units**: account-number string.
- **Safe range**: keep the tracked value as the `"REPLACE_ME"` placeholder.
  The real number goes in the untracked `config.local.json` only — never
  commit it. The gate hard-blocks all orders while the value is missing or
  still the placeholder, and also while `config.local.json` does not exist.
- **Default**: `"REPLACE_ME"`.

### `dry_run`

- **Purpose**: master safety switch. While `true`, the order gate blocks
  every `place_equity_order`; the run still computes the signal and journals
  the order it *would* have placed (prefixed `DRY-RUN:`). Set to `false` to
  trade real money.
- **Units**: boolean.
- **Safe range**: `true` until you have deliberately reviewed several
  dry-run journals and accept live trading.
- **Default**: `true`.

### `position_fraction`

- **Purpose**: fraction of buying power used to size a buy, before the
  `max_order_usd` cap: `size = min(position_fraction × buying_power,
  max_order_usd)`.
- **Units**: fraction of buying power, `0`–`1`.
- **Safe range**: `0 < x ≤ 1`. Slightly below `1` (e.g. `0.95`) leaves
  headroom for price movement between sizing and fill.
- **Default**: `0.95`.

### `max_order_usd`

- **Purpose**: hard dollar cap on any single buy. Enforced mechanically by
  the order gate: a buy with `dollar_amount` above this is blocked.
- **Units**: US dollars (notional).
- **Safe range**: small relative to money you can afford to lose; the
  dedicated account's funding remains the ultimate loss cap.
- **Default**: `550`.

### `kill_drawdown_pct`

- **Purpose**: kill-switch threshold. On each run, if portfolio
  `total_value < hwm × (1 − kill_drawdown_pct/100)` (where `hwm` is the
  high-water mark in `state/state.json`), the run sets `halt: true` and the
  gate blocks all further orders until you manually reset the state file.
- **Units**: percent drawdown from the high-water mark.
- **Safe range**: roughly `5`–`25`. Lower halts sooner; for reference the
  full SPY backtest's worst drawdown is about 15%.
- **Default**: `15`.

### `max_option_premium_usd`

- **Purpose**: hard cap on the total premium (price × quantity × 100) of any
  single option buy-to-open. Enforced mechanically by
  `scripts/option_gate.py`; if the key is missing the gate blocks all option
  orders (options trading "not enabled").
- **Units**: US dollars.
- **Safe range**: small relative to the dedicated account's funding — a long
  option's premium is its max loss.
- **Default**: `500` (lowered from `1500` to fit a small dedicated account; see
  `config.json`).

### `max_option_contracts`

- **Purpose**: hard cap on contracts per option order, enforced by
  `scripts/option_gate.py`. Missing key blocks all option orders.
- **Units**: contracts.
- **Safe range**: `1`–`5` for a small account.
- **Default**: `1` (lowered from `2` to fit a small dedicated account).

### `paper`

- **Purpose**: paper-fleet simulation knobs for
  `scripts/run_strategies.py` — `starting_cash` per book,
  `position_fraction` and `slippage_bps` for equity fills, `option_alloc`
  (fraction of book cash spent per option entry) and `option_spread_take`
  (fraction of the half-spread paid on option fills; `0` fills at mid, `1`
  at bid/ask).
- **Safe range**: paper money — affects comparison realism, not real risk.
  Keep `slippage_bps`/`option_spread_take` non-zero or results flatter.
- **Default**: `{"starting_cash": 10000, "position_fraction": 0.95,
  "slippage_bps": 2.0, "option_alloc": 0.35, "option_spread_take": 0.25}`.

### `strategies`

- **Purpose**: the paper fleet — one entry per strategy:
  `{enabled, kind, symbol(s), signal, right?, params}`. Evaluated by
  `scripts/run_strategies.py` only; the live trading path (decide.py + the
  order gates) does not read it. See
  [strategies.md](strategies.md) for every strategy and its params.
- **Default**: 5 equity + 5 options strategies, all enabled.

### `entry_rsi`

- **Purpose**: entry threshold. Buy only when `RSI(2) < entry_rsi` and the
  close is above the `sma_trend` SMA.
- **Units**: RSI points, `0`–`100`.
- **Safe range**: roughly `5`–`30`. Lower is stricter (fewer, higher-quality
  entries); higher trades more often with weaker per-trade expectancy.
  Sweep alternatives with `uv run scripts/backtest.py --entry-rsi <x>`.
- **Default**: `10.0`.

### `scale_rsi`

- **Purpose**: backtest-only threshold for the optional second tranche:
  when `RSI(2) < scale_rsi` the backtest scales from half to full size.
  Read by `scripts/backtest.py`; the live run does not scale in.
- **Units**: RSI points, `0`–`100`.
- **Safe range**: below `entry_rsi`, or it never adds selectivity. Override
  per-sweep with `uv run scripts/backtest.py --scale-rsi <x>`.
- **Default**: `5.0`.

### `slippage_bps`

- **Purpose**: backtest cost assumption — slippage charged per side on every
  simulated fill. Read by `scripts/backtest.py` only; it has no effect on
  live orders.
- **Units**: basis points per side (1 bp = 0.01%).
- **Safe range**: `1`–`5` is realistic for liquid ETF market orders; `0`
  overstates results.
- **Default**: `2.0`.

### `sma_trend`

- **Purpose**: long-trend filter length for `scripts/decide.py` — entries
  are allowed only while the close is above this simple moving average.
  `decide.py` refuses to run with fewer than `sma_trend + 5` daily bars of
  history. (`scripts/backtest.py` currently uses a fixed 200-day SMA.)
- **Units**: trading days.
- **Safe range**: long enough to be a genuine trend filter (~100–250).
  Changing it diverges the live signal from the published backtest.
- **Default**: `200`.

### `sma_exit`

- **Purpose**: exit moving-average length for `scripts/decide.py` — when
  holding, sell the full position once the close is above this SMA.
  (`scripts/backtest.py` currently uses a fixed 5-day SMA.)
- **Units**: trading days.
- **Safe range**: short (~3–10); the strategy's edge is a quick
  mean-reversion exit, not a trend ride.
- **Default**: `5`.

## Local overrides: `config.local.json`

`config.local.json` is an untracked (gitignored) file next to `config.json`
that holds machine-local values — above all the real `account_number`, which
must never be committed.

How it works (`load_config` in `scripts/order_gate.py`):

- `config.json` is loaded first, then `config.local.json` (if present) is
  **deep-merged** over it: nested objects merge recursively, every other
  value in the local file replaces the tracked one.
- The merged result is what the order gate and the trading run use.
  `scripts/decide.py` and `scripts/backtest.py` read the tracked
  `config.json` directly and ignore local overrides.
- The gate fails closed: if `config.local.json` is missing, or the merged
  `account_number` is empty or `"REPLACE_ME"`, every order is blocked.

Minimal setup:

```bash
echo '{"account_number": "<your account number>"}' > config.local.json
```

Inspect the effective merged config at any time:

```bash
python3 scripts/load_config.py
```
