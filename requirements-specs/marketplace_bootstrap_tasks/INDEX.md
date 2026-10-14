# Marketplace bootstrap tasks

Status: `[ ]` pending · `[~]` in progress · `[x]` complete · `[!]` blocked · `[?]` decision needed

| Priority | Task | Status | Confidence |
|---|---|---|---|
| P0 | 01 — Remove manual-secret startup dependency | [x] | High: Databricks injects a unique app service-principal identity. |
| P0 | 02 — Add Lakebase and explicit temporary setup storage | [x] | High: follows the Databricks Apps Lakebase credential flow. |
| P1 | 03 — Bind data-quality resources by ID | [x] | High: app resources expose stable IDs and paths through `valueFrom`. |
| P0 | 04 — Verify, publish, and exercise the deployed app | [~] | Medium: repository checks are local; deployment also depends on workspace state. |

Execution order: 01 → 02 → 03 → 04.

## Progress log

- 2026-10-14 — Epic opened after the deployed app failed because four manually managed secret resources were unresolved. The approved direction is zero per-install secret creation, Databricks SSO/app identity, Lakebase when attached, and an explicit temporary setup mode before persistent storage is attached.
- 2026-10-14 — Task 01 complete: `start.py` now derives its JWT signing key from the Databricks-injected app identity, password-admin bootstrap is opt-in, and request logging no longer records forwarded access-token headers.
- 2026-10-14 — Task 02 complete: runtime storage resolution supports an existing explicit database, Lakebase with per-connection short-lived credentials, and a clearly labelled non-persistent setup database when no state resource is attached. `/health` reports the selected mode and durability.
- 2026-10-14 — Task 03 complete: file quality uses attached job/warehouse IDs in native-app mode and fails with actionable configuration errors instead of selecting the first visible workspace resource. `app.yml` keys now match the public setup notebook instructions.
- 2026-10-14 — Task 04 in progress: Python compilation, 9 focused tests, dependency resolution for Databricks Python 3.11, and the Next.js production build pass. Repository-wide frontend lint still reports the pre-existing 59 errors/27 warnings in unrelated UI files.
- 2026-10-14 — First live deployment reached FastAPI initialization but remained behind a 502 while synchronous SQL-warehouse auto-discovery held the ASGI startup lifecycle open. Moved that network operation to a named background thread with explicit exception logging; catalog discovery can finish independently without blocking app availability.
