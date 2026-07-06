# Lanes

Prompt files for the six headless Claude Code lanes, given verbatim to
`claude -p` by `ops/run-lane.sh`:

- `research.md` — premarket market scan and candidate brief
- `thesis.md` — turn research into theses (entry/exit/invalidation)
- `risk.md` — adversarial approve/shrink/veto against envelope and budgets
- `execution.md` — place approved orders via Robinhood MCP (3x/day);
  RISK's verdicts are its only queue
- `review.md` — postmarket reconciliation, grading, daily digest
- `improve.md` — weekly self-tuning within the envelope (`improve/*` branches)

Every lane records its run in `lane_runs` via the `trader lane` CLI and
hands off JSON artifacts through the DB. Contracts and the run protocol
are documented in `docs/lanes.md`. The IMPROVE lane may edit these prompts
(with evidence, on `improve/*` branches) but must preserve each lane's
artifact contract and hard-rules sections.
