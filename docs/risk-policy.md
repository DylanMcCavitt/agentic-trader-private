# Risk policy

One page: what constrains this trader, who can change what, and how orders
are physically prevented from violating the rules.

## The envelope (human-only)

`trader/envelope.py` defines the hard outer bounds. It is constants, not
config; only a human commit may change it. The IMPROVE lane tunes params
*inside* these bounds via `param_history` rows (every write validated by
`trader/params.py`):

| Limit | Envelope | Start |
|---|---|---|
| Options sleeve budget | 10–35% of account | 25% |
| Per-position max | 2–8% of account | 5% |
| Concurrent positions | 2–8 | 5 |
| Sleeve drawdown halt | 10–25% from sleeve HWM | 15% |
| Trades per day | 1–6 | 3 |
| Options DTE window | 5–60 days | 7–45 |
| **Account kill-switch** | **FIXED 30% from HWM** | not tunable, ever |

## Sleeves

Two virtual sleeves inside the single brokerage account, created by
`trader sleeves init`: equity (budget = 1 − options fraction, long
equities/ETFs only, inverse ETFs for bearish) and options (long single-leg
calls/puts only). Each sleeve has its own budget, high-water mark, and
drawdown halt. Exposure and P&L are derived from canonical fills
(`trader sleeves status`); pending gate-approved orders count against the
budget so the gates cannot over-commit between reconciliations.

## Gates (the trust boundary)

Every live order passes through a PreToolUse hook — `trader/gates/equity_gate.py`
or `trader/gates/option_gate.py` — matched against the Robinhood MCP order
tools. The gates are human-only code; no lane, agent, or IMPROVE commit may
edit, disable, or route around them. They **fail closed**: malformed input,
DB errors, unknown account state, or an uncovered calendar year all deny.

Checks, in order: account kill-switch → ref_id present → sleeve exists and
not halted → market open (NYSE hours, holidays, early closes; `zoneinfo`) →
order shape (long-only; options: single-leg buy-to-open / sell-to-close,
call/put only) → DTE window (options buys) → trades/day counter → quote
freshness (recorded in DB within 10 minutes) → liquidity floors on opening
buys (equity: price ≥ $5, avg daily dollar volume ≥ $50M; options: open
interest ≥ 100, relative bid-ask spread ≤ 10%) → per-position cap → sleeve
budget. Exits (sell-to-close) are never blocked by liquidity floors, DTE,
position count, or budget — only by the kill-switch, halted sleeve, market
hours, and holding-verification checks.

Every verdict (allow, deny, dry-run) is recorded in the `orders` table with
the full composed payload and per-check results, keyed by the agent-supplied
`ref_id`. A ref_id that was approved once can never be reused; a denied
ref_id may be retried. With `dry_run` on (the default until M5), orders that
pass every check are still denied and recorded as `simulated`.

## Kill-switches

- **Account**: equity ≥ 30% below its high-water mark halts everything.
  Fixed in the envelope; human-only.
- **Sleeve**: sleeve value ≥ its halt fraction below the sleeve HWM halts
  that sleeve. The halt **latches** — recovery does not auto-resume; a human
  clears the flag.

The execution lane feeds equity via `trader kill-switch update --equity X
[--equity-sleeve Y --options-sleeve Z]`; `trader kill-switch status` exits
nonzero when anything is halted. Unknown equity (never fed) counts as
halted.

## Reconciliation

`trader reconcile` (broker order JSON via file/stdin) matches broker orders
to gate `ref_id`s, writes canonical fills, and updates order status. Any
broker order without a matching ref_id (**unauthorized**) or gate-approved
order the broker never saw is flagged loudly: nonzero exit plus a `flagged`
`lane_runs` event row. A flagged reconciliation means stop trading and
investigate.

## Human-only surface

`trader/envelope.py`, `trader/gates/` (including the market calendar and
kill-switch), CI, and the repo agent contract (`AGENTS.md`). Everything
else the IMPROVE lane may propose on `improve/*` branches, within the
envelope, with evidence.
