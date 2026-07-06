# PRD 0001 — Control plane + safety layer for Robinhood Agentic Trading

- **Status:** Draft v2 — reframed for production scope (for review)
- **Date:** 2026-06-22
- **Source plan:** approved ultraplan `wiggly-hatching-popcorn` · in-repo vision `docs/product-vision.md`
- **ADRs honored:** `docs/decisions/0001-allocator-replay-findings.md`, `docs/decisions/0002-allocator-switch-hysteresis.md`
- **Open spike (gates the live tier only):** `docs/spikes/robinhood-oauth.md`
- **Depth:** M1 (paper-first wedge) specified to acceptance-criteria resolution; **M2–M4 captured as epics** to be expanded into their own PRDs.
- **Decisions are provisional.** The choices here (AWS-native, multi-tenant, paper-first, daily-EOD cadence) reflect current intent and are explicitly **subject to change** as the spikes resolve and scope firms up.

> **Framing.** This PRD specs a **production, multi-tenant platform** — the control plane + safety layer for Robinhood agentic trading. The existing single-host `launchd` → `run.sh` → `claude -p` → deterministic-gate harness in this repo is a **reference implementation / proof-of-concept seed** of its one piece of real IP — a *deterministic, model-independent trust boundary on an untrusted trading agent* — **not the product.** We **preserve and elevate the core engine; we retire the harness** (at the M2 cutover). The ambition is the full AWS-native, multi-tenant SaaS; **M1 is the first shippable slice of it, not the ceiling.** Different scope, production build.

---

## Problem Statement

*From the user's perspective.*

Robinhood Agentic Trading (launched 2026-05-27) lets me authorize an MCP agent to trade a dedicated, self-funded, isolated account. But Robinhood gives me the broker, not the safety. If I want to run an agent on autopilot today I have to:

- **Babysit my own machine.** Schedule it, keep it awake, keep a token alive, hope nothing crashes mid-session.
- **Trust the model's prose.** Robinhood's pre-trade `review_equity_order` is the only checkpoint; there is no deterministic, code-enforced limit I control — no per-trade cap, no daily cap, no drawdown auto-halt, no symbol allowlist, no trading-window or stale-quote guard that the *model cannot talk its way past*.
- **Fly blind.** No real audit trail of *what the agent did and why* — signal, decision, the guardrail verdict, the fill, the resulting P&L — that I can inspect after the fact.
- **Gamble to learn.** There is no money-free way to watch a strategy run end-to-end before I risk real capital, and no honest, forward-tested track record to choose a strategy from.

And if I am just curious, I have no safe on-ramp: every path involves real money and real setup.

## Solution

*From the user's perspective.*

**A managed control plane and safety layer for Robinhood agentic trading** — pick a proven, backtested strategy (or bring my own), run it safely on autopilot, and see exactly what my agent did and why. Guardrails enforced in code, not in a prompt.

1. **Paper-first on-ramp (no money, no Robinhood account, no blockers).** I sign in, browse a strategy library with *honest* backtest stats, deploy a strategy to a **paper book**, and watch a run unfold: signal → guardrail decision (allow / block, with the reason) → order → fill → P&L → an immutable audit timeline — with a one-click **kill-switch**. A 30-second **historical-replay** mode fast-forwards an agent trading a strategy through real history so I can see the whole value prop before committing anything.
2. **The safety layer Robinhood doesn't provide.** Every order the agent proposes passes through a deterministic **PolicyEngine** *before* it can reach the broker. Per-trade cap, daily cap, drawdown auto-halt, symbol allowlist, no-margin, trading-window, stale-quote / price-tolerance, once-per-day, dry-run default. A blocked order is final and logged. The engine **fails closed**: any error, missing config, or crash blocks rather than allows.
3. **A managed runtime (later milestone).** When I'm ready for real money, I authorize my own Robinhood Agentic account, the platform holds my token securely, runs my agent on a schedule in the cloud, and the same PolicyEngine sits on the live `place_equity_order` path. No Mac to babysit.
4. **Self-host, too.** The safety engine, strategies, paper/backtest engines, and a one-click runner are **open source (Apache-2.0)**; a self-hoster runs them against their own Robinhood account with their own token.

Because I authorize **my own** agent on **my own** isolated, self-funded account, the platform never custodies money and is not a broker/RIA — a far lower regulatory barrier than "we trade your money."

---

## User Stories

*Grouped for readability; numbered continuously. M1 (paper-first) stories first and exhaustive; M2–M4 stories sketch the epics.*

### Onboarding & auth (M1)

1. As a prospective user, I want to sign up and sign in with email/password (or social), so that I can try the product without installing anything.
2. As a signed-in user, I want a single demo organization created for me automatically, so that I can start in seconds without org setup.
3. As a signed-in user, I want my session scoped to my own tenant, so that I never see another user's data.
4. As a curious visitor, I want a public, money-free demo URL, so that I can evaluate the product before signing up.

### Strategy library with honest stats (M1)

5. As a user, I want to browse a library of pre-built strategies (the existing equity, options, rotation books), so that I can pick one without writing code.
6. As a user, I want each strategy to show backtested stats (CAGR, Sharpe, max drawdown, switches), so that I can compare them on evidence.
7. As a user, I want the stats to be **honest** — including that the backtest does **not** beat buy-and-hold and the allocator does **not** yet add value — so that I am not misled (carry the ADR findings into the product copy).
8. As a user, I want a "past performance ≠ future results / educational, not investment advice" disclaimer on every strategy stat, so that I understand the framing.
9. As a user, I want to see each strategy's rules and parameters in plain language, so that I understand what it will do before deploying it.

### Paper books — deploy & run (M1)

10. As a user, I want to deploy a strategy to a **paper book** with a starting cash balance, so that I can run it with zero money at risk.
11. As a user, I want to trigger a run on demand, so that I can see what the agent does right now.
12. As a user, I want runs to also fire on a schedule, so that the book evolves like a live one without my involvement.
13. As a user, I want to watch a run unfold step by step — signal computed → guardrail verdict → order → fill → P&L update — so that I understand each decision.
14. As a user, I want the guardrail verdict shown inline (allowed, or blocked **with the reason**), so that I can see the safety layer working.
15. As a user, I want my paper book's positions, cash, and P&L to update after each run, so that I can track performance over time.
16. As a user, I want a high-water mark and drawdown shown on my book, so that I can see how close it is to the auto-halt threshold.

### The historical-replay demo (M1)

17. As a user, I want a "replay" mode that fast-forwards an agent trading a strategy through real historical data in ~30 seconds, so that I can see the entire value prop quickly.
18. As a user, I want the replay to reuse the same deterministic backtest/replay engine, so that what I watch matches the published stats.
19. As an evaluator, I want the replay to require no money, no Robinhood account, and no setup, so that it works as an instant demo.

### The safety layer — each guardrail as a behavior (M1)

20. As a user, I want every order the agent proposes to pass through the PolicyEngine **before** it can execute, so that the model is never the last line of defense.
21. As a user, I want a **per-trade cap** (max order size), so that a single order can't exceed my limit.
22. As a user, I want a **once-per-day cap** that the model cannot clear by rewriting its own state, so that the agent can't over-trade.
23. As a user, I want a **drawdown auto-halt (kill-switch)** that stops trading when my book falls past a threshold, so that losses are bounded.
24. As a user, I want a **symbol allowlist**, so that the agent can only trade instruments I approved.
25. As a user, I want a **trading-window guard** (regular hours only, market holidays and early closes respected), so that orders never fire at invalid times.
26. As a user, I want a **stale-quote / price-tolerance guard**, so that an order is blocked if its price is stale or deviates too far from the last quote.
27. As a user, I want **dry-run to be the default**, so that nothing executes for real until I explicitly opt in.
28. As a user, I want the safety layer to **fail closed** — any error, missing config, or crash blocks the order — so that a bug can never silently let an order through.
29. As a user, I want a manual **kill-switch** I can hit from the dashboard, so that I can halt my book instantly.
30. As a user, I want a halted book to require an explicit manual reset, so that it can't silently resume.
31. As a user, I want **canonical state (positions, high-water mark, halt, audit) written only by the platform, never by the model**, so that the agent cannot forge its own permissions.

### Observability & audit (M1)

32. As a user, I want an **immutable audit timeline** of every run — signal, decision, verdict, order, fill, P&L — so that I can reconstruct exactly what happened and why.
33. As a user, I want each blocked order recorded with its reason, so that I can see what the guardrails caught.
34. As a user, I want a notification when a run completes or a kill-switch trips, so that I don't have to watch the dashboard.
35. As a user, I want end-to-end traces of a run, so that I (or an operator) can debug a slow or failed run.

### Self-host / OSS (M1 surface, hardened later)

36. As a self-hoster, I want the safety engine, strategies, and paper/backtest engines as an Apache-2.0 package, so that I can run and audit them myself.
37. As a self-hoster, I want a one-click runner (Docker Compose), so that I can stand up the core locally without the managed cloud.
38. As a self-hoster, I want to point the core at my own Robinhood account with my own token, so that I control my own keys.
39. As a developer, I want config expressed as a typed schema that is the single source of truth, so that docs and per-tenant config never drift from the code.

### Live trading & managed runtime (M2 — epic, gated on the OAuth spike)

40. As a user, I want to authorize my own Robinhood Agentic account, so that the platform can run my agent on the real broker.
41. As a user, I want my Robinhood token held securely (encrypted, never logged), so that my credentials are safe.
42. As a user, I want a "Go Live" funnel from a paper book to a live book, so that I graduate a proven setup with one guided step.
43. As a user, I want the same PolicyEngine on the live `place_equity_order` path, so that live trading has the identical guardrails as paper.
44. As a user, I want the managed runtime to run my scheduled agent in the cloud, so that I don't babysit a machine.
45. As a user, I want distributed one-order-per-day and run-lock guarantees, so that concurrent or retried runs can't double-trade.
46. As the platform, I want the agent identity to be unable to reach the broker tool except through the gate, so that there is no bypass path.

### Multi-agent depth, marketplace & multi-tenant SaaS (M3 — epic)

47. As a user, I want a roster of specialized agents (Research, Signal, Risk, Execution, Reporting), so that runs are more capable — while the PolicyEngine remains the **only** enforcement.
48. As a strategy author, I want to publish a strategy to a marketplace, so that others can deploy it.
49. As a strategy author, I want my published strategy to earn a paper track record before it can be listed, so that the leaderboard reflects real forward performance.
50. As an org owner, I want to invite teammates and assign roles (owner/admin/trader/viewer), so that I can run this as a team with least privilege.
51. As an org admin, I want per-tenant isolation I can rely on, so that my org's data is provably separate from others'.
52. As a compliance reviewer, I want a per-tenant audit log, so that I can review activity for one org without seeing others'.

### Production polish, cost & security (M4 — epic)

53. As an operator, I want SLO dashboards, alarms, and runbooks, so that I can run the service reliably.
54. As an operator, I want blue/green deploys and a load test, so that releases are safe.
55. As an operator, I want a cost dashboard, scale-to-zero, and budget alarms, so that the always-on demo stays cheap.
56. As a security reviewer, I want a threat model and a security ownership map, so that multi-tenant isolation and token custody are auditable.
57. As an evaluator of the project, I want an architecture diagram, a recorded walkthrough, and one-command `terraform apply` with a seeded demo tenant, so that I can stand the whole thing up and judge it.

### The agent as a gated actor (cross-cutting, M1+)

58. As the platform, I want the agent to propose orders only via the broker tool, so that every write flows through the policy path.
59. As the platform, I want a proposed order that violates any policy to be denied before any fill and recorded in the audit log, so that enforcement and observability are one step.
60. As the platform, I want the policy decision to be deterministic and reproducible from its inputs, so that the same situation always yields the same verdict and the verdict is testable.

---

## Implementation Decisions

### Product shape & locked stack

- **Open-core.** OSS core (`packages/core`, Apache-2.0): deterministic safety engine, strategy library, paper/backtest/replay engines, `Broker`/`Agent` abstractions, one-click self-host runner. Managed control plane (`apps/*`, `infra/`, proprietary): AWS multi-tenant SaaS.
- **AWS-native + Amazon Bedrock** (Claude Opus 4.8 = `anthropic.claude-opus-4-8`), multi-tenant. Bedrock has **no Managed Agents / server-side tools** → self-orchestrate via Bedrock AgentCore + Claude Agent SDK tool use.
- **Decoupled frontend:** FastAPI (OpenAPI 3) backend + Next.js/TS frontend consuming a **generated typed client** (`packages/sdk-ts`).
- **Core engine preserved, not rewritten.** The equity gate, option gate, strategy/signal/backtest/paper/allocator/reconcile engines and their full test suite move into `packages/core/**` with **no behavior change**; tests stay green throughout.
- **Broker is Robinhood Agentic MCP** (`https://agent.robinhood.com/mcp/trading`), BYO isolated, self-funded account. Robinhood owns custody, execution, the funded-account loss cap, and pre-trade `review_equity_order`. The platform is **not** a broker/RIA and never custodies money.

### Prototype → production (what actually changes)

The repo is the seed; the platform is the target. The safety/strategy IP is **preserved**; everything around it is **rebuilt** for multi-tenant production.

| Concern | Prototype (today's repo) | Production platform (this PRD) |
|---|---|---|
| Tenancy | Single user, single Mac | Multi-tenant SaaS; per-tenant RLS isolation |
| Trust boundary | Local `.claude` PreToolUse hook (CLI) | Server-side **PolicyEngine** PEP — same logic, pure fn |
| Scheduling | `launchd` + `run.sh` (ET host) | EventBridge Scheduler + Step Functions (**daily EOD**) |
| State | `state/state.json` + file lock | Aurora Postgres + DynamoDB locks; model can't write canonical state |
| Config | `config.json` (+ doc drift) | Pydantic schema → per-tenant DB config (single source of truth) |
| Secrets | local `~/.secrets` / `.env` | Secrets Manager + KMS, never logged |
| Broker | Robinhood MCP only | `Broker` interface: Paper (M1) + Robinhood-MCP (M2) |
| Observability | `logs/journal.md` | Immutable `audit_log` + OTel/X-Ray + dashboard timeline |
| Runtime | `claude -p` reading `TRADER.md` | Claude on Bedrock (AgentCore) + agent system prompt |
| Delivery | one machine | Terraform + GitHub Actions CI/CD; OSS core + managed plane |

### Non-functional requirements (first-class, not deferred)

Production scope makes these requirements from M1, not M4 polish:

- **Multi-tenant isolation:** per-tenant RLS proven by test; tenant-scoped JWT claims; the agent/runtime DB role cannot write canonical tables.
- **Security & secrets:** least-privilege IAM; per-user Robinhood tokens in Secrets Manager + KMS, never logged; the agent identity reaches the broker only through the gate.
- **Safety invariants:** fail-closed PolicyEngine; dry-run default; real money walled + opt-in; deterministic, reproducible verdicts.
- **Observability:** every run fully traced (OTel → X-Ray); immutable audit log; blocked orders recorded with their reason.
- **Cost & demoability:** serverless + scale-to-zero (Aurora auto-pause, Fargate Spot, AgentCore serverless) + budget alarms; the always-on public demo stays paper-only and cheap.
- **Compliance posture:** not an RIA, never custodying; educational / not-investment-advice framing on every stat. (Deeper RBAC + per-tenant audit land in M3.)

### The trust boundary: PolicyEngine as a pure decision function (the load-bearing decision)

The agent never places an order directly. It calls `place_equity_order` (a Robinhood MCP tool); the platform sits in that path and runs the **PolicyEngine (Policy Enforcement Point)** synchronously first. The engine is promoted from today's process-level hook (stdin payload → exit 2 blocks / exit 0 allows) into a **pure, deterministic decision function** — the highest seam that survives the production rebuild and is independent of the interception mechanism:

```
# Pure, deterministic, no I/O. Lifted almost verbatim from the equity/option gate.
evaluate(policy: PolicyConfig,
         account: AccountState,         # positions, high-water mark, halt flag, last_action, last_quote
         order:   ProposedOrder,        # account, symbol, side, type, sizing, ref_id, price fields
         clock:   Clock) -> PolicyDecision   # { allow: bool, code: str, reason: str | None }

# Rules — fail-closed, first violation wins (today's order_gate.py / option_gate.py):
#   missing/invalid required config ............ block  (never allow on error)
#   dry_run engaged ............................ block  "dry_run"
#   kill-switch / halt set ..................... block  "halted: <reason>"
#   account ≠ the authorized account ........... block  "account_not_allowed"
#   symbol ∉ allowlist ......................... block  "symbol_not_allowed"
#   buy: must be market + dollar_amount sized;   block if not, or if amount > max_order_usd
#   sell: must specify quantity (full position)  block if absent
#   quote missing / stale (> quote_max_age_sec)  block  "stale_quote"
#   order price deviates > price_tolerance_pct . block  "price_out_of_tolerance"
#   market holiday / outside session (early-close aware) block
#   once-per-day cap already consumed .......... block  "max_one_per_day"
#   otherwise .................................. allow  (and consume the daily marker)
```

- **Two callers, one engine (mechanism decided by spike, not prejudged):** (v1) a Claude Agent SDK **PreToolUse hook** on `mcp__robinhood__place_equity_order` calls `evaluate`; (strategic) a runtime-agnostic **MCP proxy/gateway** wrapping Robinhood's MCP forwards read tools and calls `evaluate` before forwarding writes (maps to Bedrock AgentCore Gateway; "bring any agent, we enforce the guardrails"). Both call the same pure function. M1 uses the hook in front of the **Paper** broker; the MCP-proxy prototype and the hook-vs-proxy decision are an M2 spike.
- **The once-per-day cap is gate-owned, not model-written.** A marker the model has no write access to records "an order was allowed today (ET date)"; the cap can never depend on the model honestly recording its own permission. In the cloud this becomes a **DynamoDB** idempotency item (the distributed form of the file-lock + daily marker).
- **Canonical state is written only by the platform, never by the model.** Positions, high-water mark, halt, and the audit log are written exclusively by the API/reconciler service. Enforced at the **DB role** level: the agent identity has no write to canonical tables. This is the cloud form of "deterministic scripts write state, not model prose."

### Broker & Agent abstractions

- **`Broker` interface** with **Paper** and **Robinhood (MCP)** adapters. The PEP sits identically in front of both. M1 ships only the Paper adapter; the Robinhood adapter lands in M2. The Paper adapter is the existing paper-fleet/allocator engine behind the interface.
- **`Agent` abstraction** — the run definition (system prompt + tool set). `TRADER.md`'s literal procedure becomes the agent system prompt + the Step Functions definition; it is no longer a freeform session executing a markdown checklist.

### Data layer & multi-tenancy

- **Aurora PostgreSQL Serverless v2** with **Row-Level Security** for per-tenant isolation. Entities: `tenants`, `users`, `accounts`, `strategies`, `policies`, `orders`, `positions`, `runs`, and an **immutable `audit_log`**. SQLAlchemy + Alembic migrations; seed a demo tenant + the strategy library.
- **Tenant scoping** via JWT claims (Cognito) → a per-request tenant id set as a Postgres session variable that RLS policies key on. An agent/runtime DB role is read-only on canonical tables.
- **DynamoDB** for idempotency/run-locks (one-order-per-day marker, run lock). **S3** for backtest artifacts, journal exports, and a raw market-data lake. NAV/time-series in partitioned Postgres for v1 (Timestream is an optional later showcase, **not** v1).

### Config as the single source of truth

- The current `config.json` becomes **Pydantic settings models** = the one source of truth. DB-backed per-tenant config and the generated config docs both derive from it. This kills the existing doc drift (e.g. `max_option_premium_usd`, `max_option_contracts`) permanently rather than fixing it by hand.

### Orchestration & runtime

- **EventBridge Scheduler** (per-user schedule; **default cadence = once daily near the close / EOD**, matching the daily-bar strategies and the one-order-per-day cap; intraday cadences are a later option) → **Step Functions** per run: `Load → MarketOpen → Drawdown → AgentSession → Reconcile → Journal → Notify`. Per-user **Agent Runner** uses Claude Agent SDK on Bedrock AgentCore (Claude Opus 4.8), making MCP tool calls. **SQS** workers for async reconcile / paper-fleet eval / notify; **EventBridge** domain events. **SNS / push** notifications.
- **API Gateway** (JWT authz) in front of **FastAPI on ECS Fargate**, which hosts the PEP and the OpenAPI schema. **CloudFront + WAF** in front of the Next.js app.

### Observability, IaC, CI/CD

- OTel/ADOT → CloudWatch + X-Ray; AgentCore Observability. **Terraform** for all infra (network, cognito, rds, ecs, bedrock-agentcore, eventbridge, stepfns, s3, secrets, waf, observability, cicd) with remote state (S3 + DynamoDB lock) and GitHub OIDC. **GitHub Actions**: `terraform validate`/`plan` on PR, blue/green ECS deploy, Next deploy; secret/dep/image/IaC scanning.

### Secrets & security

- Per-user Robinhood OAuth tokens in **Secrets Manager + KMS**, **never logged** (extends the repo's `~/.secrets`/`.env` deny ethos). Least-privilege IAM; the agent identity cannot reach the broker tool except through the gate. **Bedrock Guardrails** on agent I/O + a client-side refusal-fallback middleware (Bedrock has no server-side fallback). Immutable audit log; optional human-in-the-loop approval as a policy; dry-run default; real money walled and opt-in.

### Decommission timing (preserve until cutover)

- The single-host harness (`com.example.agentic-trader.plist`, `install-launchd.sh`, `timezone.sh`, the ET-host requirement, `run.sh`'s launchd timeout/lock/reaper, `TRADER.md`-as-procedure, and the `.claude/settings.json` PreToolUse hooks **as the trust boundary**) is **removed at the M2 cutover, not before**. Replaced by EventBridge Scheduler + Step Functions + DynamoDB lock + the server-side / runner PEP.

---

## Testing Decisions

### What makes a good test here

- **Test external behavior, not implementation details.** For the PolicyEngine that means: given a policy config, account state, proposed order, and a pinned clock, assert the **verdict (allow/block) and the reason code** — never internal call structure. For the API, assert request → response + audit side-effects against the OpenAPI contract. For RLS, assert what a tenant *can and cannot read*, not how the policy is wired.
- **Determinism is a feature.** The PolicyEngine and the replay/backtest engines must be deterministic from their inputs; tests pin the clock and the data so they never flake (the existing `ORDER_GATE_NOW` clock seam and the snapshotted-replay approach in ADR 0002 are the model).
- **Fail-closed is explicitly tested.** Every error path (missing config, corrupt marker, crash) must assert **block**, mirroring the existing gate's "a crashing gate must block, never allow."

### Seam strategy (preferred seams, highest first)

1. **PolicyEngine pure-decision seam (load-bearing).** Carry the existing **subprocess gate tests** (`test_order_gate.py`, `test_option_gate.py`, `test_drawdown_kill.py`) forward **green as the regression spine through the M0 relocation**, then re-express them as direct table-driven `evaluate(...)` unit tests once the gate is a library function (same cases, no subprocess). The thin PreToolUse-hook / CLI adapter is tested once for wiring.
2. **Broker-contract seam.** Test the Paper adapter against the `Broker` interface; the PEP sits identically in front of it. (Robinhood adapter contract tests in M2.)
3. **API/OpenAPI contract seam.** FastAPI TestClient + schema contract tests; the generated TS client is type-checked against the same schema.
4. **RLS tenant-isolation seam.** A query under tenant-A claims cannot read tenant-B rows; the agent/runtime role cannot write canonical tables.
5. **Agent-interception seam.** An agent that proposes a violating `place_equity_order` is denied before any fill and the block is in the audit log.
6. **Replay-determinism seam.** Reuse `test_replay_allocator.py` / `test_backtest_fleet.py`: identical inputs → identical results.
7. **e2e seam (Playwright).** Sign in → deploy to paper → run (live or replay) → gate enforces → reconcile → audit + notification visible.

### Modules tested (M1)

PolicyEngine (every block path + the allow path), Paper broker adapter, the FastAPI order/run/strategy endpoints, JWT/claims + RLS isolation, the replay/backtest engines (already covered — keep green), and the e2e walking skeleton.

### Prior art in the codebase

- `tests/test_order_gate.py` — the canonical pattern: drive the gate as the hook runner does, assert exit code + stderr reason; hermetic seams `ORDER_GATE_ROOT` (temp repo root with fixture config/state) and `ORDER_GATE_NOW` (pinned clock); no real account values, obvious placeholders. The pure-function tests inherit this table-driven, fixture-root, pinned-clock style.
- `tests/test_drawdown_kill.py` — kill-switch/halt behavior.
- `tests/test_replay_allocator.py`, `tests/test_backtest_fleet.py` — deterministic replay/backtest.
- **Baseline to keep green: 398 pytest passing**, every milestone.

### Integration & infra testing

LocalStack or an AWS test account for API + DB + queue; OpenAPI contract tests; `terraform validate`/`plan` with `tfsec`/Checkov in CI. Verify the running app with the `run` / `verify` skills + chrome-devtools.

---

## Out of Scope

- **Custody, brokerage, money movement, RIA activity.** The platform never holds funds or executes on a pooled account; the user authorizes their own agent on their own isolated Robinhood Agentic account.
- **Live Robinhood trading in M1.** M1 is paper-first with **zero** Robinhood dependency. Live trading is **M2 and is gated on the OAuth spike** resolving; if the spike fails, live may be self-host/desktop-only in v1 with the cloud providing management + observability.
- **Marketplace, leaderboard, creator rev-share, org RBAC, multi-agent roster** — all **M3**, out of M1.
- **Production polish** — SLO dashboards, blue/green, load test, cost dashboard, recorded walkthrough — all **M4**.
- **New strategies / changing the trading logic.** This is productization; the existing strategies and their honest (non-market-beating) results carry forward as-is. Improving the allocator (tighter prior, slower cadence, champion diversification) remains the future work flagged in ADR 0002 — **not** in this PRD.
- **Non-Robinhood brokers, Amazon Timestream, mobile app, tax/cost-basis reporting.** Not v1.
- **Product naming (open decision, pre-demo).** `agentic-trader` stays the OSS core name; a public product name is an **open decision to settle before the M1 public demo** — flagged, not deferred. Subject to change.

---

## Acceptance Criteria

### M1 — Paper-first wedge (the walking-skeleton; ship this for demos)

- [ ] A user can sign in (Cognito), land in an auto-created demo org, and see only their own tenant's data (RLS enforced).
- [ ] The strategy library lists the existing strategies with honest backtest stats and the ADR-derived caveats + "not investment advice" disclaimer on every stat.
- [ ] A user can deploy a strategy to a paper book with a starting balance.
- [ ] A user can run the book (on demand and on a **daily EOD schedule**) and watch: signal → **PolicyEngine verdict (allow/block + reason)** → order → fill → P&L → audit timeline.
- [ ] The PolicyEngine enforces, on the (paper) order path, every guardrail: per-trade cap, once-per-day cap (gate-owned, not model-written), drawdown auto-halt, symbol allowlist, trading-window (holiday/early-close aware), stale-quote/price-tolerance, dry-run default — and **fails closed** on any error/missing config.
- [ ] Canonical state (positions, high-water mark, halt, audit) is written only by the platform; the agent/runtime DB role cannot write it (verified by test).
- [ ] A manual kill-switch halts a book instantly; a halted book requires explicit manual reset.
- [ ] The 30-second historical-replay demo runs a strategy through real history using the existing replay engine, money-free and blocker-free.
- [ ] A run is orchestrated by Step Functions, triggered by EventBridge Scheduler, notified via SNS, and traced end-to-end (OTel → X-Ray).
- [ ] All infra is Terraform; CI/CD deploys; one cost-controlled public demo URL exists.
- [ ] **398 pytest still green**, plus new tests for the PolicyEngine-as-service, RLS isolation, the Paper broker adapter, JWT/claims, and a Playwright e2e of the walking skeleton.

### Program-level (across milestones)

- [ ] **M0 remaining:** core relocated into `packages/core` (tests green), Pydantic config schema is the single source of truth, config doc-drift gone, Terraform skeleton + GitHub OIDC + remote state, Robinhood OAuth spike executed and its decision recorded.
- [ ] **M2 (epic):** per-user Robinhood OAuth tokens in Secrets Manager + KMS; the PEP on the live `place_equity_order` path; "Go Live" funnel; distributed one-order-per-day + run-lock; WAF + CloudFront; CI security scanning; single-host harness decommissioned.
- [ ] **M3 (epic):** agent roster (enforcement still only the PolicyEngine); strategy marketplace + leaderboard (paper track record before listing); org RBAC; per-tenant isolation tests; Bedrock Guardrails.
- [ ] **M4 (epic):** SLO dashboards/alarms/runbooks; blue/green; load test; cost dashboard + scale-to-zero + budget alarms; threat model + security ownership map; architecture diagram + recorded walkthrough + one-command `terraform apply` with seeded demo tenant.

---

## Proof Plan

How agents prove the behavior works **without expanding scope**:

1. **Keep the spine green.** Every change runs `uv run pytest -q`; the 398-test baseline must stay green. The PolicyEngine relocation is proven by the *existing* gate tests passing unchanged at the new location before any re-expression.
2. **Prove the PolicyEngine at its seam.** Table-driven `evaluate(...)` cases for every block path + the allow path, including the fail-closed error paths, with a pinned clock and fixture state — the `test_order_gate.py` style. No new strategy logic.
3. **Prove isolation.** An RLS test: tenant-A claims cannot read tenant-B rows; the agent/runtime role cannot write canonical tables.
4. **Prove the contract.** OpenAPI contract tests via FastAPI TestClient; the generated TS client type-checks against the same schema.
5. **Prove the walking skeleton.** A Playwright e2e: sign in → deploy to paper → run (or replay) → observe a gate **block in dry-run** → reconcile → audit + notification visible in the dashboard, fully traced. Verify the running app with the `run` / `verify` skills + chrome-devtools.
6. **Prove determinism.** Replay/backtest tests assert identical inputs → identical outputs (existing tests carried forward).
7. **Prove infra.** `terraform validate`/`plan` + `tfsec`/Checkov green in CI; the demo URL stands up from `terraform apply` against a test account / LocalStack.
8. **Scope discipline:** any finding that implies new trading logic, a new strategy, or allocator improvement is logged as future work (ADR 0002), **not** built here.

---

## Further Notes

- **The OAuth spike gates only the live tier.** Paper-first (M1) has no Robinhood dependency, so the spike runs in parallel and never blocks the wedge. Outcomes and the managed-tier architecture decision (web-flow vs per-user container vs self-host) are tracked in `docs/spikes/robinhood-oauth.md`.
- **Intellectual honesty is a product asset.** ADR 0001/0002 establish that the backtest does not beat buy-and-hold and the allocator does not yet add value; the champion-switching churn was damped (`HYSTERESIS = 3.0`) but still doesn't make the allocator add value. This honesty carries into every strategy stat and disclaimer — it is a credibility feature, not a thing to hide.
- **The existing repo is the seed, not the product.** The single-host harness proved the core IP (a deterministic, model-independent trust boundary). This PRD productizes that IP at production scope; we preserve the engine and the test suite, and retire the harness at M2.
- **Natural stop/ship point.** M1 is a complete, blocker-free, demo-ready paper-first product and the OSS core on its own — the place to stop if priorities tighten.
- **Run cadence (provisional).** Default is a single daily run near market close (EOD), consistent with the daily-bar strategies and the once-per-day cap; intraday cadences are a later option. Subject to change.
- **Decisions here are provisional.** This revision reflects current intent (AWS-native, multi-tenant, paper-first, daily EOD) and is explicitly subject to change as the OAuth spike and M0 work resolve.
- **Reframing provenance.** This **v2** reframes the PRD toward production scope — platform-first (repo = reference implementation), NFRs promoted to first-class, and a prototype→production delta added.
- **Next step:** decompose this PRD into one-branch/one-PR issue packets with `to-issues` / `issue-bootstrap`, sequencing **M0-remaining → M1 → M2 (gated on the OAuth spike) → M3 → M4**, per the owner's Agent Workflow Standard (one issue → one worktree → one branch → one PR). M2–M4 epics here are intentionally lower-resolution and should each get their own PRD before build.
- **Owner rules honored:** conventional commit prefixes; never co-author with Claude; `git add <specific-files>`; never force-push main; never `--no-verify`. Per-user tokens belong in Secrets Manager + KMS, never the repo or logs.
