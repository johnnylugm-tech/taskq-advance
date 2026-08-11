# Specification Tracking Matrix — taskq-api

> Round 2 · Phase 1 deliverable. 1-row-per-FR + 1-row-per-NFR mapping for the
> `taskq-api` service. **Source of truth for FR/NFR inventory**: `SRS.md`,
> which transcribes verbatim from `SPEC.md` per `R-CANONICAL-INTERP-001`.
> **Source of truth for machine-refreshed Status**:
> `harness/core/traceability/build_traceability.py` + `quality_manifest.json`
> (run by `advance-phase`). This file is the human-readable view; the Status
> column is overwritten on the next `advance-phase`.

## Project Info
- Project Name: taskq-api
- Version: v1.0.0
- Phase: 1 (Requirements)
- Round: 2 of 3
- Canonical spec: `SPEC.md` (project root)
- Created: 2026-08-07

## Status Legend

| Status | Meaning |
|--------|---------|
| DRAFT | Phase 1 — text-only entry; no code/test exists yet. Default starting value. |
| IN_PROGRESS | Code/module exists; tests not yet green. |
| Implemented | Code exists; awaiting machine-refresh. |
| Verified | Code + tests exist and pass (machine-refreshed by `build_traceability`). |
| Deferred | Explicitly deferred to a later phase (see SRS.md §7). |

> **Read this BEFORE editing a Status cell.** `advance-phase` calls
> `build_traceability` → `spec_tracking_render.refresh_status_table` which
> overwrites every Status cell in-place from the live code/test scan. A
> hand-edit to "Verified" without backing code + tests is silently reverted.
> Semantic columns (Spec Description / Intent Class / Decision Framework /
> Notes) are the only ones this file owns.

## Specification Status

| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |
|-------|-----------------|--------------|-------------------|--------|-------|
| FR-01 | Task Resource CRUD API — `POST/GET/LIST/DELETE /v1/tasks`, cursor pagination, scope gating, 422/404/409 problem+json. | Capability / behaviour | Spec-driven; AC-1.1..AC-1.7 each map 1:1 to `SPEC.md` §8 acceptance rows. | VERIFIED | Source: `SPEC.md` §3 FR-01; cross-cuts NFR-01 (constant SQL count). |
| FR-02 | Task Execution Endpoint — `POST /v1/tasks/{id}/run` returns 202; `asyncio.create_subprocess_exec` (no `shell=True`); state machine `pending → running → done/failed/timeout`; results written to `task_results`. | Capability / async subprocess | Spec-driven; subprocess-orphan test (`SPEC.md` §8 #25) is the load-bearing AC. | VERIFIED | Source: `SPEC.md` §3 FR-02. |
| FR-03 | API Key Authentication — `X-API-Key` header, SHA-256 hashed keys, `hmac.compare_digest`, revocation via `revoked_at`, plaintext printed exactly once at creation. | Security control | Threat-model + spec; `SPEC.md` §8 #5, #18 are the binding ACs. | VERIFIED | Source: `SPEC.md` §3 FR-03; cross-cuts NFR-02, NFR-04. |
| FR-04 | Scope Authorisation — `read < write < admin` hierarchy, single dependency in `api/deps.py`, 403 body must not reveal resource existence. | Security / access control | Spec hierarchy + single-dependency invariant (route-coverage test asserts every `/v1` route passes the same dep). | VERIFIED | Source: `SPEC.md` §3 FR-04; cross-cuts NFR-02. |
| FR-05 | Rate Limiting — per-token token bucket persisted in DB, single-transaction row-level lock, 429 + `Retry-After`, `/healthz` & `/readyz` exempt. | Operational / security | Spec-driven; `SPEC.md` §8 #9 is the binding AC. NFR-99-01 owns the exact lock mechanism. | VERIFIED | Source: `SPEC.md` §3 FR-05. |
| FR-06 | Persistence Layer and Transaction Boundaries — `repository/` only; one `Session` per request; explicit commit/rollback via context manager; `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`; no string-concatenated SQL; no N+1. | Architecture / data-access quality | Spec + N+1 guard; SQLAlchemy event-listener count assertion (`SPEC.md` §8 #14, #17). | VERIFIED | Source: `SPEC.md` §3 FR-06; cross-cuts NFR-01, NFR-02, NFR-06. |
| FR-07 | Schema Migration — Alembic v1→v2→v3, each reversible; v3 moves `tasks.result_json` into `task_results` with real data migration; round-trip `upgrade head → write sample → downgrade -1 → upgrade head` must leave every column byte-identical. | Architecture / data | Round-trip reversibility (`SPEC.md` §8 #12, #13); no `op.execute("DROP TABLE ...")` shortcuts. | VERIFIED | Source: `SPEC.md` §3 FR-07; cross-cuts NFR-03, NFR-09. |
| FR-08 | Asynchronous Executor — `asyncio.TaskGroup`, `TASKQ_MAX_CONCURRENT` cap with queuing, graceful drain up to `TASKQ_DRAIN_TIMEOUT`, timeouts via `process.kill()` + `await process.wait()`, `CancelledError` must propagate. | Async correctness | Spec-driven; `SPEC.md` §8 #25 (orphan check) + CancelledError-propagation test. | VERIFIED | Source: `SPEC.md` §3 FR-08; cross-cuts NFR-03. |
| FR-09 | Health Checks and Observability — `/healthz` (200 ok), `/readyz` (503 on DB unreachable OR `alembic current` ≠ head), `/v1/metrics` (admin scope). | Operational / readiness | Spec-driven fail-closed semantics; `SPEC.md` §8 #10, #11 are the binding ACs. | VERIFIED | Source: `SPEC.md` §3 FR-09. |
| FR-10 | Error Contract (RFC 7807) — all non-2xx are `application/problem+json`; required fields `type/title/status/detail/instance/correlation_id`; `X-Correlation-Id` echoed in response + log; mapping 422/401/403/404/409/429/503/500; no leak of stack/SQL/path. | Contract / standard | RFC 7807 + spec; `SPEC.md` §8 #19 is the binding AC. | VERIFIED | Source: `SPEC.md` §3 FR-10; cross-cuts NFR-02. |
| NFR-01 | Performance and Query Efficiency — `GET /v1/tasks/{id}` p95 < 30 ms; `GET /v1/tasks?limit=50` p95 < 80 ms at 10,000 rows; list endpoint SQL statement count constant (N+1 guard). | Performance | pytest-benchmark (`SPEC.md` §8 #14, #15) + SQLAlchemy event-listener count. | DRAFT | Dimension: `performance`. Source: `SPEC.md` §4 NFR-01. |
| NFR-02 | HTTP and Data-Layer Security — no `shell=True`/`eval(`/`exec(`; no string-concatenated SQL; SHA-256 hashed keys + `hmac.compare_digest`; 403 leaks nothing; CORS deny-by-default; 0 HIGH/MEDIUM `bandit`. | Security | grep + `bandit` + integration; `SPEC.md` §8 #16, #17, #18, #19, #23. | DRAFT | Dimension: `security`. Source: `SPEC.md` §4 NFR-02. |
| NFR-03 | Error Handling, Transactions, and Async Correctness — explicit tx boundaries; no bare `except:`/`except Exception: pass`; `CancelledError` propagates; DB failure → `/readyz` 503 (no infinite silent retry); timeouts kill children; failed migration rolls back. | Reliability / async correctness | ast scan + integration; `SPEC.md` §8 #25 + FR-06/07/08/09 cross-cuts. | DRAFT | Dimension: `error_handling`. Source: `SPEC.md` §4 NFR-03. |
| NFR-04 | Sensitive Data Redaction — `stdout_tail`/`stderr_tail`/log/error body regex `(sk-…\|token=…\|Bearer …\|postgres://…)` → wholesale `[REDACTED]`; DB password never in log/error/metrics; API key plaintext printed exactly once. | Security / data-handling | Unit test for redaction + log/metrics scan; `SPEC.md` §8 #20. | DRAFT | Dimension: `security`. Source: `SPEC.md` §4 NFR-04. |
| NFR-05 | Documentation Coverage — 100% public functions/classes carry `[FR-XX]` or `[NFR-XX]` docstring; every API endpoint in `/openapi.json` with `summary` + `description`. | Documentation | ast-docstring scan + `/openapi.json` assertion. | DRAFT | Dimension: `documentation`. Source: `SPEC.md` §4 NFR-05. |
| NFR-06 | Architecture Layering Contract — `.importlinter` declares `api > service > repository > models`; forbidden contract bans `sqlalchemy` outside `repository/`; `lint-imports` exits 0; no downgrade paths (no deletion, no wildcard `ignore_imports`). | Architecture | `lint-imports` gate; `SPEC.md` §8 #21. | DRAFT | Dimension: `architecture_constraints`. Source: `SPEC.md` §4 NFR-06. |
| NFR-07 | Dependency and License Compliance — `==` pin in `requirements.txt`; fully pinned `requirements.lock`; allowlist (MIT/BSD-2/BSD-3/Apache-2.0/PSF); whole-tree scan via `pip-licenses --format=json --with-system`; SBOM at `08-config/SBOM.json`. | Licensing / compliance | `pip-licenses` whole-tree + lock-file pinning; `SPEC.md` §8 #22. | DRAFT | Dimension: `license_compliance`. Source: `SPEC.md` §4 NFR-07. |
| NFR-08 | Mutation Testing — `features.mutation_testing: true`; `mutmut run` + `mutmut results` ≥ 70; scope = `service/` + `repository/` with runtime-budget rationale recorded in `harness_config.json`. | Test quality | `mutmut run` + score parse; `SPEC.md` §8 #24. | DRAFT | Dimension: `mutation_testing`. Source: `SPEC.md` §4 NFR-08. |
| NFR-09 | Verification Honesty (Zero-Skip Rule) — 0 `pytest.skip` / `skipif` / `xfail` / assertion-free stubs; `pytest -q` shows `skipped = 0`; anti-fabrication (no `--ignore`/`-k`/`--deselect`/`collect_ignore`); FR-07 migration tested against real SQLite file; `TRACEABILITY_MATRIX.md` `VERIFIED` only set after tests actually pass. | Test quality / verifiability | pytest collector scan + ast-assertions + real-DB round-trip; `SPEC.md` §8 #1, #12. | DRAFT | Dimension: `test_assertion_quality`. Source: `SPEC.md` §4 NFR-09. |
| NFR-10 | Integration Coverage — `tests/integration/` line coverage ≥ 80%; `httpx.AsyncClient(transport=ASGITransport(app))` driver (no direct handler calls); coverage spans full CRUD chain + one example each of 401/403/404/409/422/429/503 + migration round-trip + rate-limit trigger/recovery + graceful drain. | Integration | pytest-cov integration suite; `SPEC.md` §8 #3. | DRAFT | Dimension: `integration_coverage`. Source: `SPEC.md` §4 NFR-10. |
| NFR-11 | Readability — project MI (LLOC-weighted) ≥ 80; per-function CC ≤ 10; each file ≤ 400 lines; each directory ≤ 15 files; each API handler ≤ 40 lines (business logic in `service/`). | Maintainability | readability-v2 tool; thresholds set per dimension. | DRAFT | Dimension: `readability`. Source: `SPEC.md` §4 NFR-11. |
| NFR-12 | System Verification Target — `Makefile` `verify-system` target chains `alembic upgrade head` → full test suite → service start + `/healthz`/`/readyz` smoke → `alembic downgrade base` then `upgrade head`; exits 0 and prints `verify-system: PASS`. | Verifiability | `make verify-system` exit code + stdout scan; `SPEC.md` §8 #27. | DRAFT | Dimension: `execute_verification_target`. Source: `SPEC.md` §4 NFR-12. |

## Module Ownership (informational, §5)

> Each FR/NFR is assigned to a primary implementation module in `03-development/src/`.
> Ownership is recorded here for cross-document consistency with `TRACEABILITY_MATRIX.md`
> §5.3 and `SAD.md`; changes here are tracked in the Update Log below.

| FR/NFR | Primary Module(s) | Co-owner Module(s) | Notes |
|--------|-------------------|--------------------|-------|
| FR-01 | `taskq_api.api.tasks` | `taskq_api.service.tasks`, `taskq_api.repository.task_repo` | CRUD handler + paged-list repo. |
| FR-02 | `taskq_api.service.runner` | `taskq_api.api.tasks` | Subprocess invocation + run history endpoint. |
| FR-03 | `taskq_api.service.auth` | `taskq_api.repository.key_repo` | Single dependency in `taskq_api.api.deps`. |
| FR-04 | `taskq_api.api.deps` | — | Single auth/authz decision point; route-coverage test invariant. |
| FR-05 | `taskq_api.service.ratelimit` | `taskq_api.repository.rate_repo` | DB-persisted bucket + row-level lock (NFR-99-01). |
| FR-06 | `taskq_api.repository.session` | `taskq_api.repository.task_repo` | Session-per-request context manager. |
| FR-07 | `migrations/versions/v1_initial`, `migrations/versions/v2_tags`, `migrations/versions/v3_split_results` | — | Round-trip reversibility is the load-bearing invariant. |
| FR-08 | `taskq_api.service.runner` | — | TaskGroup + drain + timeout kill sequence. |
| FR-09 | `taskq_api.api.health` | — | `/healthz`, `/readyz`, `/v1/metrics`. |
| FR-10 | `taskq_api.errors` | `taskq_api.api.deps` | RFC 7807 problem+json + correlation-id. |
| NFR-01 | `taskq_api.repository.task_repo` | `taskq_api.api.tasks` | `selectinload`/`joinedload` + event-listener count. |
| NFR-02 | cross-cutting | — | grep + bandit + integration; enforcement via `harness/SKILL.md`. |
| NFR-03 | cross-cutting | `taskq_api.repository.session`, `taskq_api.service.runner` | ast-error-handling + integration. |
| NFR-04 | `taskq_api.errors` | `taskq_api.service.runner`, `taskq_api.config` | Redaction regex applied to stdout/stderr/log/error. |
| NFR-05 | cross-cutting | — | ast-docstring scan + openapi.json assertion. |
| NFR-06 | `.importlinter` (project root) | — | `lint-imports` gate. |
| NFR-07 | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | — | `pip-licenses --with-system`. |
| NFR-08 | `service/`, `repository/` (scope) | `.methodology/harness_config.json` | `mutmut run` ≥ 70. |
| NFR-09 | `03-development/tests/` | — | Zero-skip pytest collector scan. |
| NFR-10 | `03-development/tests/integration/` | — | `httpx.ASGITransport`. |
| NFR-11 | cross-cutting | — | readability-v2 thresholds. |
| NFR-12 | `Makefile` | — | `verify-system` chain. |

## Out-of-Scope (round 2)

These are explicitly NOT FRs/NFRs for `taskq-api` (carried over from
`SRS.md` §6 and `SPEC.md` §1 for completeness of the tracking matrix):

- TypeScript service (round 3, deferred).
- `taskq-plus` CLI (round 1, already delivered).
- Horizontal-scaling implementation details beyond per-token bucket + `pool_pre_ping`.
- Multi-tenant API-key scoping beyond the three-tier hierarchy.
- Distributed task scheduling (execution is local-process).
- Webhook / push-notification delivery on task completion.

## Update log

| Date | Change | By |
|------|--------|----|
| 2026-08-07 | Initial creation — 22-row matrix (FR-01..FR-10, NFR-01..NFR-12) populated from `SRS.md`; Module Ownership §5 populated; Status column seeded at `DRAFT` (machine-refresh will overwrite on `advance-phase`). | Agent A (Requirements Engineer) |