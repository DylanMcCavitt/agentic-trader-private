# Go-live checklist

Day 0 (the dress rehearsal) is a full scheduled trading day with `dry_run`
ON: real research, real theses, real gates — simulated orders. This
checklist covers verifying the rehearsal, flipping live, week-1 half caps,
and graduating to the full envelope.

## First thing to watch (8:30 tomorrow)

The one path that could not be verified in advance: **headless `claude`
with the Robinhood MCP connector under launchd**. The premarket job fires
at 8:30 ET; if the connector is unauthenticated in a headless/launchd
context, RESEARCH fails loudly with `Robinhood MCP unreachable` and you get
a macOS notification. If that happens: open Claude, re-auth the Robinhood
connector (see `docs/runbook.md`), then rerun `ops/run-lane.sh chain-premarket`
manually.

## After the dress rehearsal (post-16:30)

Verify, in order:

- [ ] **Lane runs**: a completed `lane_runs` row for each of research,
      thesis, risk, review, and three execution runs:
      `uv run trader lane check research|thesis|risk|execution|review`
      (each exits 0). Any missing row should have produced a notification —
      a missing row WITHOUT a notification is a silent failure: treat it as
      a blocker and debug `ops/run-lane.sh` + `logs/launchd/` before going
      live.
- [ ] **Artifacts**: `state/artifacts/<today>/` has research, thesis, risk,
      execution, review JSON matching the contracts in `docs/lanes.md`;
      verdict `thesis_id`s match thesis `id`s.
- [ ] **Simulated orders recorded**: if RISK approved anything and
      EXECUTION acted, the `orders` table has rows with status `simulated`
      (gate-passed) and/or `denied` (gate-rejected with reasons). Zero
      orders is acceptable only if the verdicts artifact shows nothing
      approved or blockers explain why.
- [ ] **Kill-switch fed**: `uv run trader kill-switch status` shows real
      equity/HWM (not "unknown") — proof EXECUTION fed portfolio data.
- [ ] **Quotes recorded**: `quotes` table has fresh rows from the
      execution sessions (proof of the quote-before-order discipline).
- [ ] **Digest + journal**: `logs/digest/<today>.md` exists;
      `journal/<YYYY>/<MM>.md` has today's section; digest notification
      arrived.
- [ ] **Reconciliation ran**: REVIEW's artifact has a `reconciliation`
      block. With dry_run on there are no broker orders, so it must be
      clean (simulated orders are not expected at the broker — only
      `pending` gate-approved ones are).
- [ ] **No stuck runs**: no `lane_runs` row left in status `running`.

Fix anything broken and, if the failure was in lanes/gates/runner, repeat
the rehearsal for one more day before flipping.

## Flip procedure (morning of live day 1, before 8:30)

1. Robinhood connector auth check: start `claude` in this repo, fetch a
   SPY quote via the Robinhood MCP tools. Re-auth if needed.
2. Sleeve init confirmation: `uv run trader sleeves status` shows both
   sleeves, correct budgets, nothing halted (except options if you are
   about to start the ramp — see below).
3. Set week-1 half caps + equity-only start:
   `uv run trader ramp start`
   (per-position 2.5%, max 3 concurrent positions, options sleeve latched
   halted; all envelope-validated, recorded in `param_history`).
4. Confirm: `uv run trader params show` shows 0.025 / 3;
   `uv run trader kill-switch status` shows the options sleeve HALTED.
5. Flip live: `uv run trader dry-run off --reason "go-live day 1 at week-1 half caps"`
6. Confirm: `uv run trader dry-run status` prints OFF.

## Week 1 (half caps)

- Days 1-2: equity sleeve only. Day 3, before 9:45:
  `uv run trader ramp options-on`.
- Daily: read the digest, check reconciliation was clean
  (`lane_runs` flagged rows / REVIEW artifact), check notifications.
- Any gate bug, unauthorized order, or reconciliation mismatch RESETS the
  clean-day count and pauses the ramp until understood.

## Full envelope — after 5 clean trading days

A clean day = all scheduled lanes completed, reconciliation clean (no
unauthorized/missing orders), no gate malfunction (denies with wrong
reasons, allows that should have denied), no kill-switch/sleeve-halt
surprises.

After 5 consecutive clean days:

```sh
uv run trader ramp full     # restores per-position 5%, 5 positions
```

Verify with `uv run trader params show`. The change lands in
`param_history` with evidence; the next journal write records it.
