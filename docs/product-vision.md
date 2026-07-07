# Product vision

> The control plane + safety layer for **Robinhood agentic trading**. Pick a
> proven, backtested strategy (or bring your own), run it safely on autopilot,
> and see exactly what your agent did and why — guardrails that aren't just a
> prompt.

## Why this exists

Robinhood Agentic Trading (launched 2026-05-27) is **bring-your-own-agent** on an
isolated, self-funded account: a user opens a dedicated Agentic account, funds
only that, and authorizes an MCP agent (`https://agent.robinhood.com/mcp/trading`)
to trade it. **Robinhood is the broker** — it owns custody, execution, the
funded-account loss cap, and the pre-trade `review_equity_order` step.

What Robinhood does *not* provide is the part this repo has always been about:
**deterministic, model-independent guardrails on an untrusted agent**, plus a
managed runtime, a strategy library, and real observability. That gap is the
product. Because the user authorizes their *own* agent on their *own* account,
the platform never custodies money or acts as a broker/RIA — a far lower
regulatory barrier than "we trade your money."

## Open-core split

- **OSS core (`packages/core`, Apache-2.0):** the deterministic safety engine,
  strategy library, paper/backtest engines, broker + agent abstractions, and a
  one-click self-host runner. What a self-hoster runs against their own Robinhood
  account — and the credibility artifact.
- **Managed control plane (`apps/*`, `infra/`):** AWS multi-tenant SaaS — Cognito
  auth, dashboard, per-user agent runners, Secrets-Manager-held Robinhood tokens,
  strategy marketplace, billing, observability.

## The trust boundary (the moat)

The agent never places an order directly. It calls `place_equity_order` (a
Robinhood MCP tool); the platform sits in that tool-call path and runs the
**PolicyEngine** first — per-trade cap, daily limit, drawdown auto-halt, symbol
allowlist, no-margin, trading window, dry-run. A blocked action is final and
logged. Fails closed. This is today's `order_gate.py` / `option_gate.py`,
promoted from a local CLI hook to a server-side policy-enforcement point.

## Stack

AWS-native + Amazon Bedrock (Claude Opus 4.8); FastAPI (OpenAPI) backend +
Next.js/TS frontend with a generated typed client; Aurora PostgreSQL Serverless
v2 with Row-Level Security; Cognito; EventBridge Scheduler + Step Functions;
Secrets Manager + KMS; OTel/CloudWatch/X-Ray; Terraform; GitHub Actions CI/CD.
The Python deterministic core is **preserved and elevated**, not rewritten.

## Roadmap

- **M0 — Foundation & cleanup.** Monorepo restructure (core moved, tests green),
  Pydantic config schema, doc-drift fixes, ADRs, Terraform skeleton, OAuth spike.
- **M1 — Paper-first demo (the wedge).** No Robinhood dependency: sign in, browse
  the strategy library with honest stats, deploy to a paper book, watch the agent
  trade with the gate enforcing, P&L, and a full audit trail — including a
  30-second historical-replay showcase. Ships first; demoable.
- **M2 — Robinhood live + managed runtime** (gated on the OAuth spike): per-user
  tokens, the gate on `place_equity_order`, "Go Live" funnel; decommission the
  launchd/`run.sh` single-host harness.
- **M3 — Multi-agent depth + marketplace + multi-tenant SaaS.**
- **M4 — Production polish & demoability.**

## Non-negotiables

- Deterministic safety is enforced in code, never by prompt.
- Paper-first and dry-run by default; real money is opt-in and walled.
- Intellectual honesty in every strategy stat (the backtest does not beat
  buy-and-hold; the allocator does not yet add value). Educational/reference
  software; not investment advice.
