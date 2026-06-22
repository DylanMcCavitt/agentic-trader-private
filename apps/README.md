# apps/

Deployable applications — the **managed control plane** (hosted tier).

- `web/` *(planned)* — Next.js dashboard: auth, strategy library, paper books,
  run/audit timeline, kill-switch.
- `api/` *(planned)* — FastAPI service hosting the PolicyEngine policy-enforcement
  point and the OpenAPI schema.
- `worker/` *(planned)* — SQS consumers: reconcile, paper-fleet, notify.

See [docs/product-vision.md](../docs/product-vision.md).
