# Repo agent contract

This repo runs an autonomous trader. Agents (lanes, coding assistants) work
inside these rules; they are not suggestions.

## Trust boundary — human-only code

Never modify, work around, or weaken:

- `trader/envelope.py` — the hard outer envelope. Constants only, changed
  exclusively by a human commit. The account kill-switch (30% from HWM) is
  fixed and not tunable by anything automated.
- `trader/gates/` — the order gates and kill-switch enforcement (PreToolUse
  hooks). No agent edits these, disables them, or routes orders around them.

Tunable behavior belongs in database params (`trader/params.py`), which
validate against the envelope on every write.

## Where state lives

- **Postgres** is the source of truth: accounts, sleeves, theses, orders,
  fills, grades, param_history, lane_runs. `DATABASE_URL` env var; local
  default is the Docker Compose instance on port 5433. Schema changes go
  through Alembic migrations.
- `config.local.json` and `.env` hold local secrets (gitignored). Never
  commit account numbers, tokens, or credentials; refer to secrets by env
  var name only.
- `archive/` is a frozen snapshot of the previous system's journal — read-only.

## How lanes run

Each lane (research, thesis, risk, execution, review, improve) is a headless
Claude Code run launched by `ops/run-lane.sh` from a launchd schedule. Lanes
communicate only through structured artifacts in Postgres and record every
run in `lane_runs`. A lane must fail loudly (macOS notification), never
exit 0 on incomplete work.

## IMPROVE lane rules

- Proposes changes as commits on `improve/*` branches only — never directly
  on `main`.
- May change: lane prompts, playbooks, and DB params (within envelope, with
  evidence recorded in `param_history`).
- May not touch: `trader/envelope.py`, `trader/gates/`, CI, or this file.
- Self-merges only after validation checks pass.

## Development

- `uv` for env and deps; `uv run pytest` must pass before merge.
- One issue → one branch → one PR. CI (pytest + gitleaks) must be green.
