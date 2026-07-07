# Parked: SaaS vision

This directory is the **parked long-term SaaS direction** for agentic-trader: an
open-core safety layer (gates, envelope, reconciliation) plus a managed AWS
service on top. It is **not the active system** — the active system is the
personal aggressive autonomous trader described in the repo root `README.md`.

Contents:

- `product-vision.md` — the open-core + managed-service product vision
- `prd/` — PRDs written against that vision (control plane and safety layer)
- `decisions/` — ADRs from the previous allocator/paper-fleet system, kept as
  lessons learned (ref_id reconciliation, switch hysteresis, replay findings)
- `spikes/` — the Robinhood OAuth spike

Nothing here is load-bearing for the running trader. Revisit when the SaaS
milestone starts.
