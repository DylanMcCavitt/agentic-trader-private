# db/

Database schema and migrations for the control plane.

Planned: Alembic migrations, PostgreSQL Row-Level Security policies for per-tenant
isolation, and seed data (a demo tenant + the strategy library). Canonical state
(positions, high-water mark, halt, journal) is written only by the API/reconciler
service — never by the agent.

See [docs/product-vision.md](../docs/product-vision.md).
