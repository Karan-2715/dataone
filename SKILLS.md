# SKILLS — Task Playbooks

Each playbook is self-contained: read only the one matching your task. Written short on purpose — Kimchi and other small-context agents should load a single playbook, not this whole file.

Every playbook follows the same shape: **Files → Steps → Done means → Watch out**.

---

## 1. Backend API endpoint / service change

**Files:** `backend/app/api/routers/<domain>.py` (route + validation) → `backend/app/services/<domain>_service.py` (logic) → `backend/app/schemas/` (pydantic request/response) → `backend/tests/<domain>/`.

**Steps:**
1. Add/extend the pydantic schema first.
2. Implement logic in the service, not the router — routers stay thin (validate → call service → return).
3. Wire the route. Add auth dependency (`get_current_user` from `auth_service`) if it mutates data.
4. Add a test per new behavior and per rejected-input case.

**Done means:** `pytest` passes in `backend/`; new/changed endpoints have at least one happy-path and one validation-failure test; no bare `except Exception: pass`.

**Watch out:** duplicate-name / allowlist validation lives in `connectors.py` — match that pattern for new resource types. Mutating routes need role/auth gating (see `pipelines.py` execute endpoint for the pattern).

---

## 2. Database model / migration change

**Files:** `backend/app/models/<name>.py` → register in `backend/app/models/__init__.py` → any service that queries it.

**Steps:**
1. Add the SQLAlchemy model. New tables register via `Base.metadata.create_all` in `main.py` — no separate migration tool in this repo currently, so schema changes take effect on next app start.
2. If the change is additive to an existing table (new column), confirm it's nullable or has a server default — there's no backfill mechanism.
3. Update any Pydantic schema that mirrors the model.

**Done means:** app boots clean, existing tests for that model's service still pass.

**Watch out:** this repo has no Alembic-style migrations — dropping/renaming a column on a running deployment is destructive. Flag schema-breaking changes as `[!]` for human sign-off rather than shipping them silently.

---

## 3. Frontend feature (dashboard pages / schema-mapper)

**Files:** `frontend/src/app/dashboard/<feature>/` (page + `components/`) → `frontend/src/lib/api.ts` (all HTTP calls go here, nowhere else).

**Steps:**
1. Check `api.ts` for an existing call before adding a new one; add one there if missing (never inline `fetch` in a component).
2. Build the component using existing patterns in that feature's `components/` dir before introducing a new one.
3. Respect `canEdit`/role checks already present on the page (e.g. published mappings, viewer role) — don't bypass them for a new affordance.

**Done means:** `npm run lint && npm run build` clean in `frontend/`; feature manually exercised in the browser (golden path + one edge case), not just type-checked.

**Watch out:** schema-mapper state goes through `useMapping.ts`'s snapshot/optimistic-update-with-rollback pattern — new mutations should follow it, not add a parallel state path. Session-timeout handling is centralized via `setUnauthorizedHandler` in `api.ts`; don't add a second 401 handler.

---

## 4. Celery task / pipeline stage change

**Files:** `backend/app/tasks/` → `backend/app/services/pipeline_service.py` → `docker-compose.yml` (if a new periodic task needs `beat`).

**Steps:**
1. Every stage logs `logger.info("[pipeline] stage=<name>")` on entry — match existing tasks' logging shape.
2. Wrap external calls (DB, Ollama, HTTP) in the existing retry-with-backoff pattern, don't write a new one.
3. New periodic tasks need a Celery beat schedule entry and the `beat` service in `docker-compose.yml`, following the `check_schema_drift_task` precedent.
4. Any state-changing task should write an `AuditLog` entry via `audit_helper.py`.

**Done means:** task runs end-to-end against local Docker compose; failures land in `AuditLog` or a log line, never silently disappear.

---

## 5. Docker / infra change

**Files:** `<service>/Dockerfile`, `docker-compose.yml`, `.env.example`.

**Steps:** follow `prompts/11-docker-first.md` in full for this one — it's the binding contract (multi-stage builds, alpine/slim bases, no build tools in runtime image, named volumes for Postgres, health checks, non-root, service-name networking not `localhost`).

**Done means:** `docker compose up` brings up all services healthy; no secrets baked into an image layer; new service has a restart policy and (if it's user-facing or has state) a health check.

---

## 6. Security-sensitive change (auth, PII, tenant isolation, secrets)

**Files:** `backend/app/services/auth_service.py`, `security_service.py`, anything touching tenant/org scoping.

**Steps:**
1. Don't auto-implement — these need explicit human sign-off before landing. Mark the task `[!]` (blocked) or `[?]` (open, needs input) per the status legend in `requirements-specs/mapper_tasks/INDEX.md` rather than guessing.
2. If you do implement a narrow, unambiguous piece (e.g. adding a validation check), keep the diff minimal and call out exactly what it does and does not cover.

**Done means:** a human has reviewed and signed off before merge. Never mark a security/tenant-isolation task `[x]` on your own authority.

**Watch out:** `SECRET_KEY` for JWT must be overridden via env var in production — never let it fall back to a code default in a deployed environment.

---

## 7. Spec-driven multi-task epic

Use when a piece of work has more than ~3 sub-tasks (new dashboard section, TRD gap-closure pass, etc.).

**Steps:**
1. Create `requirements-specs/<epic>_tasks/INDEX.md` using the format in `requirements-specs/mapper_tasks/INDEX.md`: status legend, priority table, per-task confidence note, execution order, dated progress log.
2. Write one numbered task file per item under the same directory.
3. Work top of the priority order down; update the progress log with a dated entry per completed task (what changed, files touched, any honest caveat) — this is also what feeds `MEMORY.md`.
4. Leave `[?]`/`[!]` items open rather than forcing a low-confidence implementation.

**Done means:** every non-blocked, non-open task in the index is `[x]` with a progress-log entry; blocked/open items have a one-line reason.
