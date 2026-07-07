# Lanes: pipeline, artifact contracts, schedule

Each lane is a headless Claude Code run (`claude -p "$(cat lanes/<lane>.md)"`)
launched by `ops/run-lane.sh`. Lanes communicate only through structured
JSON artifacts stored in Postgres (`lane_runs.artifact`, via the
`trader lane` CLI) and mirrored to `state/artifacts/YYYY-MM-DD/<lane>.json`
(gitignored) for debugging. The DB is the source of truth; the files are a
convenience mirror.

## Pipeline

```
premarket 8:30 ET (one launchd job, runner chains sequentially):
  RESEARCH ──brief──▶ THESIS ──theses──▶ RISK ──verdicts──▶ (queue for the day)

9:45 / 12:30 / 15:15 ET:   EXECUTION (standalone; reads today's risk verdicts)
16:30 ET:                  REVIEW (reconcile, grade, digest)
Saturday 10:00 ET:         IMPROVE (tune params/prompts within the envelope)
```

The premarket chain aborts on the first failed lane — THESIS never runs
against a missing brief, RISK never runs against missing theses. EXECUTION
with no risk verdicts for the day manages existing positions only.

## Run protocol (every lane)

1. `RUN_ID=$(uv run trader lane record-start <lane>)` — creates a `running`
   row in `lane_runs`.
2. Do the work; write the artifact:
   `uv run trader lane artifact put <lane> --run-id $RUN_ID --file <f.json>`
   (stores JSON on the run row + mirrors to `state/artifacts/`).
3. `uv run trader lane record-end $RUN_ID --status completed --summary "..."`
   and print `LANE_COMPLETE: <lane>` as the final output line.
4. On any failure: `record-end --status failed --summary "<reason>"` and
   print `LANE_FAILED: <lane> — <reason>`.

The runner treats a lane as successful only if `trader lane check <lane>`
finds a completed `lane_runs` row for today — exit codes and sentinel
strings are secondary evidence, never the truth test.

Lanes that touch the broker (RESEARCH for quotes, EXECUTION, REVIEW) verify
Robinhood MCP connectivity with a cheap call at the START of the run and
fail loudly if the connector is down or unauthenticated. This is the
defense against the old system's silent death on expired connector auth.

## Artifact contracts

### research → `research` artifact (the brief)

```json
{
  "date": "YYYY-MM-DD",
  "market_regime_summary": "string",
  "candidates": [
    {
      "symbol": "TICKER",
      "catalyst": "why now",
      "evidence": "sources/observations",
      "momentum_metrics": {"ret_5d": 0.0, "ret_20d": 0.0, "rel_volume": 0.0, "gap_pct": 0.0},
      "suggested_sleeve": "equity | options"
    }
  ]
}
```

### thesis → `thesis` artifact

```json
{
  "date": "YYYY-MM-DD",
  "theses": [
    {
      "id": "YYYY-MM-DD-<symbol>-<n>",
      "sleeve": "equity | options",
      "symbol": "TICKER",
      "instrument": "equity | call | put",
      "direction": "long",
      "entry_plan": "string",
      "exit_target": "string",
      "invalidation": "string — MUST be checkable at 3x/day cadence",
      "sizing_suggestion": "fraction of account",
      "time_horizon": "string",
      "catalyst": "string",
      "confidence": 0.0
    }
  ]
}
```

`direction` is always `long` (bearish = inverse ETF or put). Invalidations
requiring real-time stops are invalid by contract; RISK vetoes them.

### risk → `risk` artifact (EXECUTION's queue)

```json
{
  "date": "YYYY-MM-DD",
  "kill_switch_active": false,
  "verdicts": [
    {
      "thesis_id": "...",
      "verdict": "approve | shrink | veto",
      "adjusted_size": "final fraction (approve/shrink)",
      "reasons": ["..."]
    }
  ]
}
```

Every input thesis gets exactly one verdict. EXECUTION trades only
approve/shrink verdicts at `adjusted_size`.

### execution → `execution` artifact (one per session run)

```json
{
  "date": "YYYY-MM-DD",
  "session": "open | midday | power",
  "orders_placed": [
    {"ref_id": "uuid", "thesis_id": "...", "symbol": "...", "side": "buy | sell",
     "instrument": "equity | call | put", "qty_or_notional": "...", "status": "placed | rejected"}
  ],
  "positions_managed": ["..."],
  "exits_taken": [{"ref_id": "uuid", "thesis_id": "...", "reason": "target | invalidation | time"}],
  "blockers": ["gate rejections, MCP errors, halts"]
}
```

Every order carries a fresh `uuidgen` ref_id and goes through Robinhood MCP
tools so the PreToolUse gates fire. There is no other order path.

EXECUTION's gate obligations each run: feed portfolio equity first
(`trader kill-switch update --equity X --equity-sleeve Y --options-sleeve Z`
— gates deny when account equity is unknown), and record a DB quote no
older than 10 minutes immediately before every order (`trader quotes
snapshot SYMBOL` for equities; `trader quotes record` with `kind:"option"`,
`occ_symbol`, bid/ask/open_interest for options). Order payloads must
include `ref_id`, `symbol`, `side`, `quantity`, `limit_price`, and for
options additionally `expiration_date`, `strike_price`, `option_type`, and
`position_effect` (open|close). Verdicts carry only `thesis_id`, so
EXECUTION also reads the day's thesis artifact for symbols and exit plans.

### review → `review` artifact (grades + digest)

```json
{
  "date": "YYYY-MM-DD",
  "reconciliation": {"clean": true, "mismatches": []},
  "grades": [
    {
      "thesis_id": "...",
      "score": 0,
      "what_was_right": "...",
      "what_was_wrong": "...",
      "lane_at_fault": "research | thesis | risk | execution | none",
      "lesson": "..."
    }
  ],
  "digest": {
    "account_equity": 0.0,
    "pnl_by_sleeve": {"equity": 0.0, "options": 0.0},
    "open_positions": [{"symbol": "...", "thesis_id": "...", "vs_thesis": "..."}],
    "orders_today": 0,
    "gate_rejections": 0,
    "notes": "..."
  }
}
```

Score is 0–10 on process quality, not P&L. `lane_at_fault` + `lesson` are
the IMPROVE lane's raw material.

REVIEW reconciles first — broker orders from the Robinhood MCP
`get_equity_orders` + `get_option_orders` tools, fed to `trader reconcile
--file ...` — then grades, then runs `trader digest` and
`trader journal write`.

### improve → `improve` artifact (weekly report)

```json
{
  "week": "YYYY-MM-DD",
  "trades_graded": 0,
  "win_rate": 0.0,
  "avg_score": 0.0,
  "by_lane_fault": {"research": 0, "thesis": 0, "risk": 0, "execution": 0, "none": 0},
  "changes_made": [
    {"type": "prompt | param", "target": "lanes/xxx.md | param_name",
     "rationale": "...", "evidence": "..."}
  ],
  "watch_items": ["..."]
}
```

IMPROVE commits only on `improve/*` branches (self-merged back to its base
branch only when `uv run pytest` is green), only touches `lanes/*.md` and
DB params (`trader params set`, envelope-validated, max 2/week), fires the
weekly-report notification via `ops/notify.sh`, and never touches
`trader/envelope.py`, `trader/gates/**`, `.claude/**`, `ops/**`,
`.github/**`, or CI.

## Schedule (all times ET; machine must be on America/New_York)

| launchd label | Time | Runs |
|---|---|---|
| com.agentic-trader.premarket | Mon–Fri 8:30 | `run-lane.sh chain-premarket` (research → thesis → risk) |
| com.agentic-trader.exec-open | Mon–Fri 9:45 | `run-lane.sh execution` |
| com.agentic-trader.exec-midday | Mon–Fri 12:30 | `run-lane.sh execution` |
| com.agentic-trader.exec-power | Mon–Fri 15:15 | `run-lane.sh execution` |
| com.agentic-trader.review | Mon–Fri 16:30 | `run-lane.sh review` |
| com.agentic-trader.improve | Sat 10:00 | `run-lane.sh improve` |

Install/uninstall with `ops/install.sh` / `ops/uninstall.sh` (see
`docs/runbook.md`). Market holidays are handled by the gates (no orders
pass on a closed market), not the schedule.
