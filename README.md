# agentic-trader

Autonomous RSI(2) mean-reversion swing trader for a dedicated, capped
brokerage account, executed by a scheduled headless Claude Code session via
Robinhood's official Agentic Trading MCP.

## Strategy (Connors RSI-2, long-only, SPY)

- **Entry**: close > 200-day SMA and RSI(2) < 10 → buy ~95% of buying power
  (dollar-based market order, fractional).
- **Exit**: close > 5-day SMA → sell the full position.
- Signals computed at ~3:45pm ET using the live price as a provisional close.
- Backtest (yfinance daily, 2bps slippage/side): SPY 1993–2026: 4.9% CAGR,
  Sharpe 0.79, max DD −14.8%, 77% win rate, ~8 trades/yr, avg hold 5 days,
  14% market exposure. 2020–2026: Sharpe 1.03. This strategy does NOT beat
  buy-and-hold absolutely — its appeal is high per-trade expectancy with low
  exposure and shallow drawdowns. Reproduce with `uv run scripts/backtest.py`.

## Setup

Dependencies are managed by a root `pyproject.toml` + committed `uv.lock`
(pandas, yfinance; pytest in the dev group). `uv sync` from a clean checkout
builds the env; `uv run scripts/<x>.py` resolves against the project env
automatically — no per-script inline metadata.

## How a run works

`launchd` (com.example.agentic-trader, weekdays 15:45 ET) → `run.sh`
(time-window + lock guard) → `claude -p` executes `TRADER.md` →
`scripts/decide.py` computes the signal → Robinhood MCP places/reviews orders
→ journal + state + macOS notification.

## Safety layers

1. **Robinhood-side sandbox** — the MCP can only trade in the Agentic account;
   its funding is the hard loss cap.
2. **`scripts/order_gate.py`** (PreToolUse hook, deterministic): blocks any
   order when `dry_run` or `halt` is set, wrong account/symbol, buys not
   market+dollar-sized, size > `max_order_usd`, outside market hours, or a
   second order in one day. Fails closed: a missing or unparsable
   `config.json` or `state/state.json` blocks the order outright (exit 2).
3. **Kill switch**: portfolio value 15% below high-water mark → `halt: true`,
   no further trading until manually reset in `state/state.json`.
4. **`dry_run: true`** in `config.json` — orders are reviewed and journaled
   but never placed. Flip to `false` to go live.

## Files

- `pyproject.toml` / `uv.lock` — pinned dependencies (uv-managed)
- `config.json` — symbol, sizing caps, dry_run flag, and shared strategy
  params read by both `decide.py` and `backtest.py`; `account_number` is the
  `REPLACE_ME` placeholder
  - `entry_rsi` — RSI(2) entry threshold
  - `scale_rsi` — RSI(2) threshold for the backtest's second tranche
  - `slippage_bps` — backtest slippage per side, in basis points
- `config.local.json` — untracked (gitignored) local overrides, deep-merged
  over `config.json` by the order gate and the trading run. Create it once
  with the real account: `{"account_number": "<your account number>"}`. Until
  it exists, the order gate hard-blocks every order.
- `state/state.json` — untracked (gitignored) live state: high-water mark,
  halt flag, last action. First-run bootstrap: copy the tracked example,
  `cp state/state.example.json state/state.json`. Until it exists, the order
  gate blocks every order (fail closed).
- `logs/journal.md` — one entry per run; `logs/runner.log` — scheduler output
- `TRADER.md` — the exact procedure the headless session follows

## Setup

- Install the scheduler: `bash scripts/install-launchd.sh` — substitutes this
  repo's path into `com.example.agentic-trader.plist`, installs it to
  `~/Library/LaunchAgents/`, and loads it. Safe to re-run.
- The weekday 15:45 schedule is intentional: the signal is computed at
  ~3:45pm ET using the live price as a provisional close, so orders can fill
  before the 4pm close. Don't change it without revisiting the strategy.
- Migrating from an older install under a different label? Boot out the old
  agent (`launchctl bootout gui/$UID/<old-label>`) and delete its plist from
  `~/Library/LaunchAgents/` before installing.

## Ops

- Pause: `launchctl bootout gui/$UID/com.example.agentic-trader`
- Resume: `launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.example.agentic-trader.plist`
- The Mac must be awake at 3:45pm ET; launchd fires a missed run on wake but
  `run.sh` skips it outside 15:30–15:58 ET.
- Re-auth: if the claude.ai Robinhood connector token expires, runs will
  journal MCP errors — reconnect via claude.ai → Settings → Connectors.

## Disclaimer

This project is educational/reference software for studying agentic trading
harnesses. It is not investment advice, and nothing in this repository is a
recommendation to buy or sell any security. When `dry_run` is set to `false`
in `config.json`, it trades real money in a live brokerage account — losses
are entirely possible and entirely yours. If you run it live, use a dedicated
brokerage account funded only with money you can afford to lose; the account
balance is the hard loss cap. The software is provided without warranty of any
kind (see [LICENSE](LICENSE)). The shipped default is the safe configuration:
`dry_run: true`, so no orders are ever placed until you deliberately change it.
