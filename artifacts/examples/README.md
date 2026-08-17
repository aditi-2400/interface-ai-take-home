# Hand-written example artifacts

These are hand-authored for schema reference and testing — **not** the product
of a real discovery run. Per the project's ground rules, the only artifacts
allowed to claim they came from discovery are the ones actually produced by a
live LLM-driven run against the mock app (saved to `artifacts/store/` and
backed by evidence in `/evidence/discovery/`).

- `transfer_v1.json` — a `transfer_funds` capability shaped the way a
  well-formed capability should look after the artifact-conversion step
  (deep-links straight to `/accounts/{id}/transfer` rather than re-enacting
  exploratory search-then-click navigation).
