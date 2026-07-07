# agentic-trader

An aggressive, self-improving autonomous trader for a single Robinhood
account. Runs unattended on a Mac via launchd, driven by headless Claude Code
lanes, with all durable state in Postgres and hard risk limits enforced in
code the agents cannot touch.

## How it works

**Two independent sleeves** over one brokerage account:

- **Equity sleeve** (~75%): momentum / high-beta chasing via deterministic
  screens (no static whitelist). Long-only; bearish exposure via inverse ETFs.
- **Options sleeve** (~25%): long calls/puts only, 7–45 DTE, liquidity-gated.
  Never sells naked anything.

Each sleeve has its own budget, position limits, drawdown halt, and
high-water mark, reconciled daily against the broker.

**Five lanes** (plus a weekly sixth), each a separate headless Claude Code
run passing structured artifacts through Postgres:

| Lane | Schedule | Does |
|---|---|---|
| RESEARCH | premarket 8:30 | market scan, candidate brief |
| THESIS | after research | theses with entry / exit / invalidation |
| RISK | after thesis | approve/reject against envelope + budgets |
| EXECUTION | 9:45 / 12:30 / 15:15 ET | place approved orders via Robinhood MCP |
| REVIEW | postmarket 16:30 | grade trades, daily digest |
| IMPROVE | Saturday | tune params/prompts within the envelope |

**The hard outer envelope** (`trader/envelope.py`) bounds everything the
system may do: options sleeve 10–35% of account, per-position 2–8%, 2–8
concurrent positions, 1–6 trades/day, 5–60 DTE, per-sleeve drawdown halts,
and a fixed account kill-switch at 30% below high-water mark. The IMPROVE
lane tunes runtime parameters *inside* those bounds; the bounds themselves
and the order gates are human-only code.

## Layout

```
trader/          Python package (uv): envelope, params, CLI, db models
  gates/         order gates + kill-switch (M2) — the trust boundary
  sleeves/       ledger, budgets, reconciliation (M2)
  screener/      yfinance momentum screens (M4)
  db/            SQLAlchemy models + session
alembic/         migrations
lanes/           lane prompt files (M3)
ops/             run-lane.sh, launchd plists, notifications (M3)
docs/vision/     parked long-term SaaS direction (not active)
archive/         journal/logs from the previous system
tests/           test suite
```

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), Docker, and the `claude` CLI
with the Robinhood MCP connector authenticated.

```sh
docker compose up -d        # Postgres 16 on localhost:5433
uv sync
uv run trader db upgrade    # run migrations
uv run trader sleeves init  # account row + equity/options sleeves
uv run pytest               # full suite (Postgres test needs compose up)

# sanity checks
uv run trader sleeves status
uv run trader kill-switch status   # "unknown" until execution feeds equity
uv run trader params show          # tunables + envelope bounds
uv run trader dry-run status       # defaults ON — orders simulate

ops/install.sh              # launchd schedules (machine TZ must be ET)
launchctl list | grep agentic-trader
```

With `dry_run` ON the next scheduled trading day is a dress rehearsal:
real research/theses/gates, simulated orders. Going live after that is
[docs/go-live-checklist.md](docs/go-live-checklist.md) — verification
steps, the `trader dry-run off` flip, week-1 half caps via `trader ramp
start`, and the 5-clean-days criteria for `trader ramp full`. Day-to-day
operation is [docs/runbook.md](docs/runbook.md).

State lives in Postgres (`DATABASE_URL`, default
`postgresql+psycopg://trader:trader@localhost:5433/trader`). Local secrets
live in `config.local.json` / `.env` (gitignored).
