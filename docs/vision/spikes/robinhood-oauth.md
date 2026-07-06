# Spike: Robinhood Agentic OAuth for a managed cloud runtime

- **Status:** Open — must validate before the managed *live* tier. The paper-first
  v1 wedge has **no** Robinhood dependency and is unaffected.
- **Opened:** 2026-06-22

## Question

Can a web/server-side OAuth flow obtain a **refresh token** the platform can use
to run a Robinhood Agentic agent **server-side**, on the user's behalf, on a
schedule — without a human completing a desktop/`localhost` step each run?

## Why it matters

Robinhood's Agentic Trading connects an MCP agent to a dedicated, user-funded
Agentic account (`https://agent.robinhood.com/mcp/trading`). The documented setup
(`claude mcp add robinhood-trading --transport http ...`) authenticates on a
**desktop** device and finishes by pasting a `localhost` URL back — i.e., it
appears bound to a local MCP client. A managed cloud runtime needs durable,
server-usable credentials instead.

## Outcomes

- **Web flow + refresh token supported →** clean managed live trading: store the
  refresh token in Secrets Manager + KMS; run per-user scheduled agents.
- **Not supported →** either (a) run each user's agent in a per-user isolated
  cloud container that completes the desktop-style flow once and persists the
  session, or (b) make live trading self-host/desktop-only in v1, with the cloud
  providing management + observability over the user's own runner.

## Acceptance criteria

1. Confirm whether Robinhood exposes a standard OAuth 2.0 auth-code + refresh-token
   grant for the Agentic MCP (not only the desktop/`localhost` flow).
2. If yes, complete the flow headlessly in a test harness and refresh a token.
3. Document token lifetime, refresh cadence, scopes, and revocation.
4. Decide the managed-tier architecture: web-flow vs per-user container vs self-host.

## Sources

- https://robinhood.com/us/en/agentic-trading/
- https://robinhood.com/us/en/support/articles/agentic-trading-overview/
