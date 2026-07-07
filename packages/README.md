# packages/

Reusable libraries — the **open-source core** of the platform.

- `core/` *(planned, Apache-2.0)* — the deterministic safety engine, strategy
  library, paper/backtest engines, broker + agent abstractions, and a one-click
  self-host runner. What a self-hoster runs against their own Robinhood Agentic
  account, and the credibility artifact of the open-core model.
- `sdk-ts/` *(planned)* — generated typed API client for the web app.

Until the M0 relocation lands, the core still lives in top-level `scripts/`.
See [docs/product-vision.md](../docs/product-vision.md).
