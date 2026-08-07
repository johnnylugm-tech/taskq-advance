# Software Requirements Specification (SRS) — taskq-api

> **Round 2 — Ingestion Mode.** All `### FR-01..FR-10` and `### NFR-01..NFR-12`
> are transcribed verbatim from `SPEC.md` (v1.0.0, 2026-07-30) per
> `R-CANONICAL-INTERP-001` (verbatim canonical phrase, no interpretive
> re-phrasing) and `R-NO-PRESCRIPTION-001` (no methodology/process artifacts
> in the deliverable). Canonical source: `SPEC.md` at project root,
> identified via `PROJECT_BRIEF.md` §`canonical_spec: SPEC.md`.

## 1. Introduction

### 1.1 Purpose

This SRS captures the requirements for `taskq-api`, an HTTP task-queue
service: submit, query and execute shell-command tasks over a REST API;
persist to a relational database through SQLAlchemy; evolve the schema with
Alembic; authenticate with hashed API keys, authorise by scope, and throttle
per token. Source: `SPEC.md` §1.

### 1.2 Scope

In-scope (source: `SPEC.md` §1, §2, §3, §4):

- FastAPI ASGI application invoked as `uvicorn taskq_api.app:app`
- REST API for task CRUD, task execution, runs history, health, metrics
- SQLAlchemy 2.x ORM with explicit `Session` transaction boundaries
- Alembic schema migration across three revisions, including a data-migration
  step that is reversible
- API-key authentication (SHA-256 hashed), per-token scope authorisation,
  per-token token-bucket rate limiting
- Asynchronous task execution via `asyncio.create_subprocess_exec`
- RFC 7807 (`application/problem+json`) error contract with correlation-id
- Catalog of 12 `TASKQ_*` environment variables driving runtime configuration

### 1.3 Project context

- Project name: `taskq-api`
- Phase: 1 (Requirements)
- Round: 2 of 3 (progressive harness-methodology test-bed — round 1 was
  `taskq-plus` CLI; round 3 is TypeScript, deferred)
- Companion files: `PROJECT_BRIEF.md`, `SPEC.md`, `.env.example`,
  `.importlinter`, `requirements.txt`, `alembic.ini`, `Makefile`

### 1.4 Definitions, acronyms, abbreviations

See §9 (Glossary).

### 1.5 References

- `SPEC.md` — Single Source of Truth for FR-01..FR-10 and NFR-01..NFR-12
- `PROJECT_BRIEF.md` — Phase 1 brief, lists the canonical spec and 10 / 12
  FR / NFR inventory
- `harness/harness/ssi/prompts/evaluate_dimension.md` — current ranking of
  scored `dimension` values (what the gate tooling actually scores against)

---

## 2. Constraints

The following constraints are operational boundaries the implementation must
respect and are repeated in the structure of the downstream artefacts
(`.importlinter`, `requirements.txt`, `alembic.ini`, `Makefile`). Each
constraint below cites its canonical source.

| ID | Constraint | Source |
|----|------------|--------|
| C-01 | Python 3.11; FastAPI ASGI app `uvicorn taskq_api.app:app` | `SPEC.md` §1, §2, Key Constraints |
| C-02 | SQLAlchemy 2.x with explicit `Session` transaction boundaries | `SPEC.md` §2, Key Constraints |
| C-03 | Alembic for migrations | `SPEC.md` §2, Key Constraints |
| C-04 | `asyncio.create_subprocess_exec` for task execution; `shell=True` forbidden everywhere | `SPEC.md` §2, Key Constraints |
| C-05 | Four layers `api > service > repository > models` enforced by a mandatory `.importlinter` contract; `config` and `errors` are independence modules | `SPEC.md` Key Constraints (Architecture) |
| C-06 | `sqlalchemy` may only be imported by `repository/` — ORM leakage into the business layer is the guard against a specific anti-pattern | `SPEC.md` Key Constraints (Architecture), NFR-06 |
| C-07 | API keys stored as SHA-256 hashes; compared with `hmac.compare_digest` | `SPEC.md` Key Constraints (Security), FR-03, NFR-02 |
| C-08 | 403 responses must not reveal whether the resource exists | `SPEC.md` Key Constraints (Security), FR-04 |
| C-09 | No string-concatenated SQL anywhere | `SPEC.md` Key Constraints (Security), NFR-02 |
| C-10 | CORS denies all origins by default | `SPEC.md` Key Constraints (Security), NFR-02 |
| C-11 | Error bodies must not carry stack traces, SQL or file paths | `SPEC.md` Key Constraints (Security), FR-10, NFR-02 |
| C-12 | Three Alembic revisions: v1 base, v2 tags many-to-many, **v3 moves `tasks.result_json` into a `task_results` table with real data migration**; `upgrade head` → sample write → `downgrade -1` → `upgrade head` must leave every column byte-identical | `SPEC.md` Key Constraints (Migration), FR-07 |
| C-13 | `asyncio.CancelledError` must propagate — never swallowed by `except Exception` | `SPEC.md` Key Constraints (Async correctness), FR-08, NFR-03 |
| C-14 | Task timeouts must actually kill the child process (`kill()` then `await wait()`), leaving no orphans | `SPEC.md` Key Constraints (Async correctness), FR-08, NFR-03 |
| C-15 | Shutdown drains in-flight work up to `TASKQ_DRAIN_TIMEOUT` | `SPEC.md` Key Constraints (Async correctness), FR-08 |
| C-16 | Relationship loads must be explicit (`selectinload` / `joinedload`); N+1 is an acceptance failure — the list endpoint's SQL statement count must be constant regardless of how many rows come back | `SPEC.md` Key Constraints (Query efficiency), NFR-01 |
| C-17 | `/readyz` returns 503 when the database is unreachable **or** when `alembic current` is not at head — deploying new code without running the migration must fail closed | `SPEC.md` Key Constraints (Readiness), FR-09 |
| C-18 | The three-step migration must be tested against a **real database file**, not a mock, and may not be downgraded to a skip on the grounds that "migration logic is hard to test" | `SPEC.md` Key Constraints (Verification honesty), NFR-09 |
| C-19 | Same zero-skip rule as round 1 (no `pytest.skip` / `skipif` / `xfail` / assertion-free stub) | `SPEC.md` NFR-09 |
| C-20 | `crg_cohesion_healthy` is retained at its default value — not lowered to pass the project | `SPEC.md` §10 framework alignment |

---

## 3. Functional Requirements

### FR-01: Task Resource CRUD API

**Source**: `SPEC.md` §3 FR-01.

**Endpoints**:

| Method | Path | Scope | Behaviour |
|--------|------|-------|-----------|
| `POST` | `/v1/tasks` | `write` | Create task; body validated by `TaskCreate` pydantic model |
| `GET` | `/v1/tasks/{id}` | `read` | Retrieve a single task with all fields |
| `GET` | `/v1/tasks` | `read` | Paged list, supports `?status=`, `?limit=`, `?cursor=` |
| `DELETE` | `/v1/tasks/{id}` | `admin` | Delete task (along with results rows, in the same transaction) |

**Acceptance Criteria**:

- **AC-1.1**: A `POST /v1/tasks` request with a valid `write` API key, a
  DERIVED: SPEC.md §3 FR-01 (validation rules), §8 #4 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  non-empty `command`/`name` of ≤ 1000 characters, no injection-character
  blacklist hits, and a non-duplicate `name` returns **HTTP 201** with the
  task id. Source: `SPEC.md` §3 FR-01 (validation rules), §8 #4.
- **AC-1.2**: A `POST /v1/tasks` request whose body fails the `TaskCreate`
  DERIVED: SPEC.md §SPEC.md — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  pydantic model returns **HTTP 422** + `application/problem+json`. Source:
  `SPEC.md` §3 FR-01 (validation rules), §7 422.
- **AC-1.3**: A `GET /v1/tasks/{unknown}` request returns **HTTP 404** +
  DERIVED: SPEC.md §3 FR-01, §7 404, §8 #7 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  `application/problem+json`. Source: `SPEC.md` §3 FR-01, §7 404, §8 #7.
- **AC-1.4**: A `POST /v1/tasks` with a duplicate `name` returns **HTTP 409**
  DERIVED: SPEC.md §SPEC.md — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  + `application/problem+json`. Source: `SPEC.md` §3 FR-01 (validation
  rules), §7 409, §8 #8.
- **AC-1.5**: Pagination is **cursor-based**; offset-based pagination is not
  DERIVED: SPEC.md §3 FR-01 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  used. Source: `SPEC.md` §3 FR-01.
- **AC-1.6**: Default `limit` is 50, maximum 200; values exceeding 200 return
  DERIVED: SPEC.md §3 FR-01 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  **HTTP 422**. Source: `SPEC.md` §3 FR-01.
- **AC-1.7**: The list endpoint's SQL statement count is **constant** with
  DERIVED: SPEC.md §SPEC.md — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
  respect to the number of rows returned (no N+1). Source: `SPEC.md` §3
  FR-01 (cross-cuts NFR-01), §8 #14.

### FR-02: Task Execution Endpoint

**Source**: `SPEC.md` §3 FR-02.

**Endpoints**:

- `POST /v1/tasks/{id}/run` (scope `write`) → **HTTP 202 Accepted**, body contains `run_id`
- `GET /v1/tasks/{id}/runs` (scope `read`) → run history for the task, newest first

**Behaviour**:

- Execution is performed via `asyncio.create_subprocess_exec(*shlex.split(command))`; `shell=True` is forbidden. Timeout is `TASKQ_TASK_TIMEOUT`.
- State machine: `pending → running → done | failed | timeout`.
- Results are written to the `task_results` table (the v3 schema delivered by FR-07), with fields: `exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`.

**Acceptance Criteria**:

- **AC-2.1**: A `POST /v1/tasks/{id}/run` with a valid `write` key returns **HTTP 202** with a `run_id` in the body. Source: `SPEC.md` §3 FR-02.
  DERIVED: SPEC.md §3 FR-02 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-2.2**: Process invocation uses `asyncio.create_subprocess_exec(*shlex.split(...))` — `shell=True` never appears in source (verified by `grep -rn "shell=True" 03-development/src/` returning 0 hits). Source: `SPEC.md` §3 FR-02, NFR-02, §8 #16.
  DERIVED: SPEC.md §3 FR-02, NFR-02, §8 #16 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-2.3**: A subprocess that times out is terminated by `process.kill()` followed by `await process.wait()`; no orphan processes remain. Source: `SPEC.md` §3 FR-02, FR-08, §8 #25.
  DERIVED: SPEC.md §3 FR-02, FR-08, §8 #25 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-2.4**: Terminal status of the run is recorded in the `task_results` table per the v3 schema (`exit_code`, `stdout_tail`, `stderr_tail`, `duration_ms`, `finished_at`). Source: `SPEC.md` §3 FR-02, §5.2.
  DERIVED: SPEC.md §3 FR-02, §5 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-2.5**: `GET /v1/tasks/{id}/runs` returns the task's run history ordered newest-first. Source: `SPEC.md` §3 FR-02.
  DERIVED: SPEC.md §3 FR-02 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-03: API Key Authentication

**Source**: `SPEC.md` §3 FR-03.

**Behaviour**:

- All `/v1/*` endpoints require an `X-API-Key` header; missing or invalid key → **HTTP 401** + problem+json.
- Keys are stored as SHA-256 hashes in the `api_keys` table; plaintext is never stored. Comparison uses `hmac.compare_digest` (constant-time).
- Keys are generated by `python -m taskq_api key create --scope <scope>`; plaintext is printed exactly once at creation time.
- Keys with non-null `revoked_at` are treated as invalid.
- `/healthz` and `/readyz` do not require authentication (FR-09).

**Acceptance Criteria**:

- **AC-3.1**: A `POST /v1/tasks` request without an `X-API-Key` header returns **HTTP 401** + problem+json. Source: `SPEC.md` §3 FR-03, §7 401, §8 #5.
  DERIVED: SPEC.md §3 FR-03, §7 401, §8 #5 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-3.2**: The `api_keys` table contains only `key_hash` values that are 64-character hex strings (SHA-256); no plaintext key is ever stored. Source: `SPEC.md` §3 FR-03, NFR-02, §8 #18.
  DERIVED: SPEC.md §3 FR-03, NFR-02, §8 #18 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-3.3**: Key comparison uses `hmac.compare_digest` (constant-time). Source: `SPEC.md` §3 FR-03, NFR-02.
  DERIVED: SPEC.md §3 FR-03, NFR-02 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-3.4**: A revoked key (non-null `revoked_at`) is rejected as invalid. Source: `SPEC.md` §3 FR-03.
  DERIVED: SPEC.md §3 FR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-3.5**: Plaintext key material is printed exactly once at creation time and never written to any persistent location. Source: `SPEC.md` §3 FR-03, NFR-04.
  DERIVED: SPEC.md §3 FR-03, NFR-04 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-04: Scope Authorisation

**Source**: `SPEC.md` §3 FR-04.

**Behaviour**:

- Each key carries a scope: `read` < `write` < `admin` (hierarchical containment).
- The required scope per endpoint is documented in FR-01/02; insufficient scope → **HTTP 403** + problem+json, and the body must not reveal whether the resource exists.
- Authorisation must occur in a **single middleware (dependency)**; it must not be scattered across handlers. A test asserts that every `/v1` route passes through the same dependency.

**Acceptance Criteria**:

- **AC-4.1**: A `DELETE /v1/tasks/{id}` request with a `write` (non-admin) key returns **HTTP 403**, and the body does not reveal whether the id exists. Source: `SPEC.md` §3 FR-04, §7 403, §8 #6.
  DERIVED: SPEC.md §3 FR-04, §7 403, §8 #6 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-4.2**: Authorisation is enforced by a single dependency (`api/deps.py`); a test asserts that every `/v1` route passes through the same dependency. Source: `SPEC.md` §3 FR-04.
  DERIVED: SPEC.md §3 FR-04 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-4.3**: The scope hierarchy is `read` < `write` < `admin` (each higher tier contains the lower). Source: `SPEC.md` §3 FR-04.
  DERIVED: SPEC.md §3 FR-04 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-05: Rate Limiting

**Source**: `SPEC.md` §3 FR-05.

**Behaviour**:

- Per-token token bucket: capacity `TASKQ_RATE_BURST`, refill rate `TASKQ_RATE_PER_SEC`.
- Exceeded → **HTTP 429** + problem+json + `Retry-After` header (seconds).
- Token bucket state is stored in the database (consistent across workers); updates occur in a single transaction with a row-level lock.
- `/healthz` and `/readyz` are not rate-limited.

**Acceptance Criteria**:

- **AC-5.1**: Sending more requests than `TASKQ_RATE_BURST` in a single window returns **HTTP 429** + problem+json; the response includes a `Retry-After` header. Source: `SPEC.md` §3 FR-05, §7 429, §8 #9.
  DERIVED: SPEC.md §3 FR-05, §7 429, §8 #9 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-5.2**: Token bucket state is persisted in the database; bucket updates run in a single transaction with a row-level lock. Source: `SPEC.md` §3 FR-05.
  DERIVED: SPEC.md §3 FR-05 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-5.3**: `/healthz` and `/readyz` are not subject to rate limiting. Source: `SPEC.md` §3 FR-05.
  DERIVED: SPEC.md §3 FR-05 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-06: Persistence Layer and Transaction Boundaries

**Source**: `SPEC.md` §3 FR-06.

**Behaviour**:

- All data access goes through the `repository/` layer; the service layer must not hold a `Session` directly.
- One `Session` per API request; transaction boundaries are explicit: commit on success, rollback on exception (guaranteed by a context manager).
- String-concatenated SQL is forbidden; all queries use ORM or parameterised queries (NFR-02).
- Relationship queries must use `selectinload` / `joinedload` explicitly — **N+1 is an acceptance failure** (NFR-01).
- Connection pool: `pool_size=TASKQ_DB_POOL_SIZE`, `pool_pre_ping=True`.

**Acceptance Criteria**:

- **AC-6.1**: All ORM access is reached via `repository/` modules; no `Session` is held by the service layer. Source: `SPEC.md` §3 FR-06, NFR-06.
  DERIVED: SPEC.md §3 FR-06, NFR-06 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-6.2**: Each API request is bounded by a single explicit transaction: commit on success, rollback on exception, enforced by a context manager. Source: `SPEC.md` §3 FR-06, NFR-03.
  DERIVED: SPEC.md §3 FR-06, NFR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-6.3**: All SQL is ORM-generated or parameterised — no f-string / `%` / `+` concatenation of SQL strings anywhere in `03-development/src/` (verified by code review and grep gate). Source: `SPEC.md` §3 FR-06, NFR-02, §8 #17.
  DERIVED: SPEC.md §3 FR-06, NFR-02, §8 #17 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-6.4**: The list endpoint executes a constant number of SQL statements regardless of the number of rows returned (N+1 guard). Source: `SPEC.md` §3 FR-06, NFR-01, §8 #14.
  DERIVED: SPEC.md §3 FR-06, NFR-01, §8 #14 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-6.5**: `pool_size=TASKQ_DB_POOL_SIZE` and `pool_pre_ping=True` are configured on the engine. Source: `SPEC.md` §3 FR-06.
  DERIVED: SPEC.md §3 FR-06 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-07: Schema Migration

**Source**: `SPEC.md` §3 FR-07.

**Three revisions, each with a working `downgrade`**:

| Revision | `upgrade` content | `downgrade` requirement |
|----------|-------------------|--------------------------|
| **v1** | Create `tasks`, `api_keys` tables | Drop both tables |
| **v2** | Add `tags`, `task_tags` (many-to-many) + unique index on `tasks.name` | Drop new tables and index; does not affect v1 data |
| **v3** | **Data migration**: split `tasks.result_json` into a separate `task_results` table, migrate existing data, then drop the original column | Reverse-migrate back into `tasks.result_json`, then drop `task_results`; no data loss |

**Additional rules**:

- `alembic upgrade head` and `alembic downgrade base` must both succeed.
- **Round-trip reversibility**: `upgrade head` → write sample data → `downgrade -1` → `upgrade head` must leave every column byte-identical (v3 data migration is the focus of this rule).
- Destructive shortcuts such as `op.execute("DROP TABLE ...")` are forbidden as a substitute for a real `downgrade`.
- Migration files themselves are inside test coverage (offline SQL generation + assertions).

**Acceptance Criteria**:

- **AC-7.1**: `alembic upgrade head` succeeds against a fresh empty database. Source: `SPEC.md` §3 FR-07.
  DERIVED: SPEC.md §3 FR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-7.2**: `alembic downgrade base` succeeds and leaves no residual tables. Source: `SPEC.md` §3 FR-07, §8 #13.
  DERIVED: SPEC.md §3 FR-07, §8 #13 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-7.3**: The round-trip `upgrade head → write sample → downgrade -1 → upgrade head` against a real SQLite database file leaves every column byte-identical. Source: `SPEC.md` §3 FR-07, §8 #12.
  DERIVED: SPEC.md §3 FR-07, §8 #12 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-7.4**: No `op.execute("DROP TABLE ...")` is used to circumvent a real `downgrade`. Source: `SPEC.md` §3 FR-07.
  DERIVED: SPEC.md §3 FR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-7.5**: Migration logic is covered by tests that exercise the offline SQL or a real DB — not skipped on the grounds of "migration logic is hard to test". Source: `SPEC.md` §3 FR-07, NFR-09.
  DERIVED: SPEC.md §3 FR-07, NFR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-08: Asynchronous Executor

**Source**: `SPEC.md` §3 FR-08.

**Behaviour**:

- Background execution is managed by `asyncio.TaskGroup`; on shutdown the service must **graceful drain** (await in-flight tasks up to `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked `interrupted`).
- Concurrency cap `TASKQ_MAX_CONCURRENT`; over-cap requests are queued, not unboundedly spawned.
- Task timeouts are implemented via `asyncio.wait_for`; on timeout the child process must be actually terminated (`process.kill()` then `await process.wait()`), leaving no orphans.
- Cancellation semantics: `asyncio.CancelledError` must propagate — it must not be swallowed by `except Exception` (NFR-03).

**Acceptance Criteria**:

- **AC-8.1**: At most `TASKQ_MAX_CONCURRENT` tasks execute concurrently; over-cap requests are queued. Source: `SPEC.md` §3 FR-08.
  DERIVED: SPEC.md §3 FR-08 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-8.2**: On shutdown, in-flight tasks are awaited up to `TASKQ_DRAIN_TIMEOUT`; tasks exceeding the budget are marked `interrupted`. Source: `SPEC.md` §3 FR-08.
  DERIVED: SPEC.md §3 FR-08 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-8.3**: A timed-out task has its child process terminated by `process.kill()` then `await process.wait()`; no orphan processes remain. Source: `SPEC.md` §3 FR-08, FR-02, §8 #25.
  DERIVED: SPEC.md §3 FR-08, FR-02, §8 #25 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-8.4**: `asyncio.CancelledError` is never swallowed by `except Exception`; it propagates. Source: `SPEC.md` §3 FR-08, NFR-03.
  DERIVED: SPEC.md §3 FR-08, NFR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-09: Health Checks and Observability

**Source**: `SPEC.md` §3 FR-09.

**Endpoints**:

| Endpoint | Auth | Behaviour |
|----------|------|-----------|
| `GET /healthz` | none | Process alive → 200 `{"status":"ok"}` |
| `GET /readyz` | none | DB connection available **and** `alembic current` == head → 200; otherwise **503**, body explains which check failed |
| `GET /v1/metrics` | `admin` | Task counts (by status), execution latency percentiles, rate-limit rejection counts |

**Acceptance Criteria**:

- **AC-9.1**: `GET /readyz` returns **503** with a body that names the failure when the database is unreachable. Source: `SPEC.md` §3 FR-09, §8 #10.
  DERIVED: SPEC.md §3 FR-09, §8 #10 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-9.2**: `GET /readyz` returns **503** with a body that names the failure when `alembic current` is not at head. Source: `SPEC.md` §3 FR-09, §8 #11.
  DERIVED: SPEC.md §3 FR-09, §8 #11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-9.3**: A deployment that omits running the migration fails closed at `/readyz`. Source: `SPEC.md` §3 FR-09.
  DERIVED: SPEC.md §3 FR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-9.4**: `/healthz` and `/readyz` are not subject to authentication or rate limiting. Source: `SPEC.md` §3 FR-09, FR-03, FR-05.
  DERIVED: SPEC.md §3 FR-09, FR-03, FR-05 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-9.5**: `/v1/metrics` requires `admin` scope and exposes task counts, execution latency percentiles, and rate-limit rejection counts. Source: `SPEC.md` §3 FR-09.
  DERIVED: SPEC.md §3 FR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### FR-10: Error Contract (RFC 7807)

**Source**: `SPEC.md` §3 FR-10.

**Behaviour**:

- All non-2xx responses have `Content-Type: application/problem+json`.
- Body fields: `type` (URI), `title`, `status`, `detail`, `instance`, `correlation_id`.
- `detail` must not leak internal details: it must not contain SQL statements, stack traces, file paths, or database schema descriptions.
- `correlation_id` appears both as the `X-Correlation-Id` response header and in the server log, allowing end-to-end stitching.
- Error code mapping: 422 validation / 401 unauthenticated / 403 insufficient scope / 404 unknown resource / 409 name conflict / 429 rate limited / 503 not ready / 500 other.

**Acceptance Criteria**:

- **AC-10.1**: Every non-2xx response is `application/problem+json` with the fields `type`, `title`, `status`, `detail`, `instance`, `correlation_id`. Source: `SPEC.md` §3 FR-10.
  DERIVED: SPEC.md §3 FR-10 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-10.2**: A triggered 500 response body contains no stack trace, no SQL, no file path. Source: `SPEC.md` §3 FR-10, NFR-02, §8 #19.
  DERIVED: SPEC.md §3 FR-10, NFR-02, §8 #19 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-10.3**: The `correlation_id` value from the response body matches the `X-Correlation-Id` response header and a log entry. Source: `SPEC.md` §3 FR-10.
  DERIVED: SPEC.md §3 FR-10 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-10.4**: The status code mapping matches the §7 table (422 / 401 / 403 / 404 / 409 / 429 / 503 / 500). Source: `SPEC.md` §3 FR-10, §7.
  DERIVED: SPEC.md §3 FR-10, §7 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

---

## 4. Non-Functional Requirements

> **Dimension mapping rule** (per `R-CANONICAL-INTERP-001` and the note in
> `SPEC.md` §4): every NFR's `dimension:` field is checked against the
> current `### <dimension>` headers in
> `harness/harness/ssi/prompts/evaluate_dimension.md`. All twelve canonical
> dimensions are present in the current roster, so no `dimension note` is
> required. Coverage of each AC against the dimension's actual gate check
> is asserted in the AC lines below.

### NFR-01: Performance and Query Efficiency

- **dimension**: `performance`
- **type**: `performance`
- **Source**: `SPEC.md` §4 NFR-01.

**Acceptance Criteria**:

- **AC-N01.1**: `GET /v1/tasks/{id}` p95 < 30 ms at 10,000 rows (measured via ASGI transport, excluding network). Source: `SPEC.md` §4 NFR-01, §8 #15.
  DERIVED: SPEC.md §4 NFR-01, §8 #15 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N01.2**: `GET /v1/tasks?limit=50` p95 < 80 ms at 10,000 rows. Source: `SPEC.md` §4 NFR-01, §11.
  DERIVED: SPEC.md §4 NFR-01, §11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N01.3**: The list endpoint's SQL statement count is **constant** with respect to the number of rows returned (N+1 guard) — verified via a SQLAlchemy event listener count assertion. Source: `SPEC.md` §4 NFR-01, §8 #14.
  DERIVED: SPEC.md §4 NFR-01, §8 #14 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **Coverage note**: the `performance` dimension in `evaluate_dimension.md` is tool-scored via pytest-benchmark / js-bench; the AC above is aligned with that tool.

### NFR-02: HTTP and Data-Layer Security

- **dimension**: `security`
- **type**: `security`
- **Source**: `SPEC.md` §4 NFR-02.

**Acceptance Criteria**:

- **AC-N02.1**: `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` returns 0 hits. Source: `SPEC.md` §4 NFR-02, §8 #16.
  DERIVED: SPEC.md §4 NFR-02, §8 #16 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.2**: No string-concatenated SQL (f-string / `%` / `+`) anywhere in `03-development/src/` — verified by grep + code review. Source: `SPEC.md` §4 NFR-02, §8 #17.
  DERIVED: SPEC.md §4 NFR-02, §8 #17 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.3**: API keys are stored hashed and compared with `hmac.compare_digest` (FR-03). Source: `SPEC.md` §4 NFR-02, FR-03.
  DERIVED: SPEC.md §4 NFR-02, FR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.4**: 403 responses do not reveal resource existence (FR-04). Source: `SPEC.md` §4 NFR-02, FR-04, §8 #6.
  DERIVED: SPEC.md §4 NFR-02, FR-04, §8 #6 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.5**: Error bodies contain no stack / SQL / path (FR-10). Source: `SPEC.md` §4 NFR-02, FR-10, §8 #19.
  DERIVED: SPEC.md §4 NFR-02, FR-10, §8 #19 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.6**: CORS denies all origins by default; the allowlist is taken from `TASKQ_CORS_ORIGINS`. Source: `SPEC.md` §4 NFR-02.
  DERIVED: SPEC.md §4 NFR-02 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N02.7**: `bandit -r 03-development/src/` reports 0 HIGH and 0 MEDIUM issues. Source: `SPEC.md` §4 NFR-02, §8 #23.
  DERIVED: SPEC.md §4 NFR-02, §8 #23 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-03: Error Handling, Transactions, and Async Correctness

- **dimension**: `error_handling`
- **type**: `reliability`
- **Source**: `SPEC.md` §4 NFR-03.

**Acceptance Criteria**:

- **AC-N03.1**: Each request's transaction boundaries are explicit: commit on success, rollback on exception, enforced by a context manager (FR-06). Source: `SPEC.md` §4 NFR-03, FR-06.
  DERIVED: SPEC.md §4 NFR-03, FR-06 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N03.2**: No bare `except:` or `except Exception: pass` exists in the codebase. Source: `SPEC.md` §4 NFR-03.
  DERIVED: SPEC.md §4 NFR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N03.3**: `asyncio.CancelledError` is never swallowed — it must be re-raised. Source: `SPEC.md` §4 NFR-03, FR-08.
  DERIVED: SPEC.md §4 NFR-03, FR-08 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N03.4**: A database connection failure surfaces as `/readyz` 503 with an explicit `detail`; no silent infinite retry. Source: `SPEC.md` §4 NFR-03, FR-09.
  DERIVED: SPEC.md §4 NFR-03, FR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N03.5**: Task timeout terminates the child process without leaving orphans (FR-08). Source: `SPEC.md` §4 NFR-03, FR-08, §8 #25.
  DERIVED: SPEC.md §4 NFR-03, FR-08, §8 #25 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N03.6**: A failed migration rolls the database back to the previous revision (FR-07). Source: `SPEC.md` §4 NFR-03, FR-07.
  DERIVED: SPEC.md §4 NFR-03, FR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-04: Sensitive Data Redaction

- **dimension**: `security`
- **type**: `security`
- **Source**: `SPEC.md` §4 NFR-04.

**Acceptance Criteria**:

- **AC-N04.1**: Before writing/emitting, `stdout_tail`, `stderr_tail`, log lines, and error bodies are checked against the regex `(sk-[A-Za-z0-9_-]{8,}|token=\S+|Bearer\s+\S+|postgres(ql)?://[^\s]+)`; matching lines are replaced wholesale with `[REDACTED]`. Source: `SPEC.md` §4 NFR-04.
  DERIVED: SPEC.md §4 NFR-04 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N04.2**: The database connection string (including password) does not appear in any log, error message, or `/v1/metrics` response. Source: `SPEC.md` §4 NFR-04, §8 #20.
  DERIVED: SPEC.md §4 NFR-04, §8 #20 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N04.3**: API key plaintext is printed exactly once at `key create` and never written to any persistent location. Source: `SPEC.md` §4 NFR-04, FR-03.
  DERIVED: SPEC.md §4 NFR-04, FR-03 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-05: Documentation Coverage

- **dimension**: `documentation`
- **type**: `documentation`
- **Source**: `SPEC.md` §4 NFR-05.

**Acceptance Criteria**:

- **AC-N05.1**: Every public function/class has a docstring referencing `[FR-XX]` or `[NFR-XX]`; coverage is 100%. Source: `SPEC.md` §4 NFR-05.
  DERIVED: SPEC.md §4 NFR-05 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N05.2**: Every API endpoint has `summary` and `description` in the OpenAPI schema; `/openapi.json` is asserted in tests. Source: `SPEC.md` §4 NFR-05.
  DERIVED: SPEC.md §4 NFR-05 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-06: Architecture Layering Contract

- **dimension**: `architecture_constraints`
- **type**: `layering`
- **Source**: `SPEC.md` §4 NFR-06.

**Acceptance Criteria**:

- **AC-N06.1**: A `.importlinter` file declares the layers contract `api > service > repository > models`; upper layers may import lower layers, lower layers must not import upper layers; `config` and `errors` are independence modules. Source: `SPEC.md` §4 NFR-06.
  DERIVED: SPEC.md §4 NFR-06 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N06.2**: A forbidden contract in `.importlinter` bans `sqlalchemy` imports outside `repository/`. Source: `SPEC.md` §4 NFR-06.
  DERIVED: SPEC.md §4 NFR-06 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N06.3**: `lint-imports` exits 0; deleting `.importlinter`, wildcard `ignore_imports`, or downgrading the contract are not permitted substitution paths. Source: `SPEC.md` §4 NFR-06, §8 #21.
  DERIVED: SPEC.md §4 NFR-06, §8 #21 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-07: Dependency and License Compliance

- **dimension**: `license_compliance`
- **type**: `licensing`
- **Source**: `SPEC.md` §4 NFR-07.

**Acceptance Criteria**:

- **AC-N07.1**: All runtime dependencies are pinned with `==` in `requirements.txt`; transitive dependencies are fully pinned in `requirements.lock`. Source: `SPEC.md` §4 NFR-07.
  DERIVED: SPEC.md §4 NFR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N07.2**: Allowed licenses: MIT / BSD-2-Clause / BSD-3-Clause / Apache-2.0 / PSF; any other license disqualifies the dependency. Source: `SPEC.md` §4 NFR-07.
  DERIVED: SPEC.md §4 NFR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N07.3**: The license scan covers the entire dependency tree (direct + transitive), evidenced by `pip-licenses --format=json --with-system`. Source: `SPEC.md` §4 NFR-07, §8 #22.
  DERIVED: SPEC.md §4 NFR-07, §8 #22 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N07.4**: An SBOM is emitted at `08-config/SBOM.json` containing `name`, `version`, `license`, and `direct|transitive` per dependency. Source: `SPEC.md` §4 NFR-07.
  DERIVED: SPEC.md §4 NFR-07 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-08: Mutation Testing

- **dimension**: `mutation_testing`
- **type**: `mutation`
- **Source**: `SPEC.md` §4 NFR-08.

**Acceptance Criteria**:

- **AC-N08.1**: `.methodology/harness_config.json` sets `features.mutation_testing: true`. Source: `SPEC.md` §4 NFR-08.
  DERIVED: SPEC.md §4 NFR-08 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N08.2**: `mutmut run` followed by `mutmut results` reports a mutation score ≥ 70. Source: `SPEC.md` §4 NFR-08, §8 #24.
  DERIVED: SPEC.md §4 NFR-08, §8 #24 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N08.3**: The mutation run is scoped to `service/` and `repository/`; the scope is recorded in `harness_config.json` with a runtime-budget rationale. Source: `SPEC.md` §4 NFR-08.
  DERIVED: SPEC.md §4 NFR-08 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-09: Verification Honesty (Zero-Skip Rule)

- **dimension**: `test_assertion_quality`
- **type**: `testability`
- **Source**: `SPEC.md` §4 NFR-09.

**Acceptance Criteria**:

- **AC-N09.1**: No verification test for any FR/NFR is `pytest.skip` / `skipif` / `xfail` / an assertion-free stub. Source: `SPEC.md` §4 NFR-09.
  DERIVED: SPEC.md §4 NFR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N09.2**: `pytest 03-development/tests -q` reports `skipped = 0`. Source: `SPEC.md` §4 NFR-09, §8 #1.
  DERIVED: SPEC.md §4 NFR-09, §8 #1 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N09.3**: Every test function contains at least one `assert` (`zero_assert == 0`). Source: `SPEC.md` §4 NFR-09, §11.
  DERIVED: SPEC.md §4 NFR-09, §11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N09.4**: Anti-fabrication clause: tests are not excluded via `--ignore` / `-k` / `--deselect` / `collect_ignore` / removing a directory from `testpaths`. Source: `SPEC.md` §4 NFR-09.
  DERIVED: SPEC.md §4 NFR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N09.5**: The three-step migration (FR-07) is tested against a real SQLite database file (not an in-memory mock); round-trip reversibility is verified by actual data comparison. "Migration logic is hard to test" is not an acceptable reason to downgrade to skip. Source: `SPEC.md` §4 NFR-09, FR-07, §8 #12.
  DERIVED: SPEC.md §4 NFR-09, FR-07, §8 #12 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N09.6**: `TRACEABILITY_MATRIX.md` `VERIFIED` flags are only set after tests actually run and pass. Source: `SPEC.md` §4 NFR-09.
  DERIVED: SPEC.md §4 NFR-09 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-10: Integration Coverage

- **dimension**: `integration_coverage`
- **type**: `integration`
- **Source**: `SPEC.md` §4 NFR-10.

**Acceptance Criteria**:

- **AC-N10.1**: `03-development/tests/integration/` line coverage ≥ 80%. Source: `SPEC.md` §4 NFR-10, §8 #3.
  DERIVED: SPEC.md §4 NFR-10, §8 #3 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N10.2**: Integration tests are driven via `httpx.AsyncClient(transport=ASGITransport(app))`; no direct handler-function calls. Source: `SPEC.md` §4 NFR-10.
  DERIVED: SPEC.md §4 NFR-10 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N10.3**: Coverage includes: full CRUD chain, one example each of 401 / 403 / 404 / 409 / 422 / 429 / 503, migration round-trip, rate-limit trigger and recovery, graceful drain. Source: `SPEC.md` §4 NFR-10.
  DERIVED: SPEC.md §4 NFR-10 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-11: Readability

- **dimension**: `readability`
- **type**: `maintainability`
- **Source**: `SPEC.md` §4 NFR-11.

**Acceptance Criteria**:

- **AC-N11.1**: Project MI (LLOC-weighted) ≥ 80. Source: `SPEC.md` §4 NFR-11.
  DERIVED: SPEC.md §4 NFR-11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N11.2**: Per-function CC ≤ 10. Source: `SPEC.md` §4 NFR-11.
  DERIVED: SPEC.md §4 NFR-11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N11.3**: Each file ≤ 400 lines; each directory ≤ 15 files. Source: `SPEC.md` §4 NFR-11.
  DERIVED: SPEC.md §4 NFR-11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N11.4**: Each API handler ≤ 40 lines; business logic must live in `service/`. Source: `SPEC.md` §4 NFR-11.
  DERIVED: SPEC.md §4 NFR-11 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

### NFR-12: System Verification Target

- **dimension**: `execute_verification_target`
- **type**: `verifiability`
- **Source**: `SPEC.md` §4 NFR-12.

**Acceptance Criteria**:

- **AC-N12.1**: `Makefile` `verify-system` target chains: `alembic upgrade head` → full test suite → service start + `/healthz`, `/readyz` smoke → `alembic downgrade base` then `upgrade head` (round-trip). Source: `SPEC.md` §4 NFR-12.
  DERIVED: SPEC.md §4 NFR-12 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.
- **AC-N12.2**: `make verify-system` exits 0 and prints `verify-system: PASS`. Source: `SPEC.md` §4 NFR-12, §8 #27.
  DERIVED: SPEC.md §4 NFR-12, §8 #27 — verbatim canonical §-row transcription; expanded into a testable AC clause per R-CANONICAL-INTERP-001.

---

## 5. Acceptance Criteria Summary

The single-machine-decidable commands listed in `SPEC.md` §8 are the
authoritative acceptance run-list. Each is preserved verbatim below; the
`#` column matches `SPEC.md` §8 row order.

| # | Command | Expected |
|---|---------|----------|
| 1 | `pytest 03-development/tests -q` | All green; `skipped` count = 0 (NFR-09) |
| 2 | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | TOTAL 100% |
| 3 | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | TOTAL ≥ 80% (NFR-10) |
| 4 | `POST /v1/tasks` (valid `write` key) | 201 + task id |
| 5 | `POST /v1/tasks` (no `X-API-Key`) | 401 + problem+json |
| 6 | `DELETE /v1/tasks/{id}` (write key, non-admin) | 403; body does not reveal whether id exists |
| 7 | `GET /v1/tasks/{unknown}` | 404 + problem+json |
| 8 | `POST /v1/tasks` duplicate name | 409 |
| 9 | Consecutive requests exceeding `TASKQ_RATE_BURST` | 429 + `Retry-After` header |
| 10 | Stop DB then `GET /readyz` | 503; detail names DB-unavailable |
| 11 | `alembic downgrade -1` then `GET /readyz` | 503; detail names migration-not-at-head |
| 12 | `alembic upgrade head` → write sample → `downgrade -1` → `upgrade head` | Sample data column-identical (v3 data migration reversible — FR-07) |
| 13 | `alembic downgrade base` | exit 0; no residual tables |
| 14 | `GET /v1/tasks?limit=50` (10,000 rows) SQL statement count | Constant (independent of row count — N+1 guard, NFR-01) |
| 15 | `GET /v1/tasks/{id}` p95 (10,000 rows) | < 30 ms (NFR-01) |
| 16 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | 0 hits |
| 17 | Scan for SQL string concatenation (f-string / `%` / `+`) | 0 hits (NFR-02) |
| 18 | Query `api_keys` table | No plaintext keys; `key_hash` is 64 hex (NFR-02) |
| 19 | Trigger 500 then inspect response body | Contains no stack / SQL / file path (FR-10 / NFR-02) |
| 20 | Logs and `/v1/metrics` full scan | No `TASKQ_DB_URL` password fragment (NFR-04) |
| 21 | `lint-imports` | exit 0; `service`/`api` importing `sqlalchemy` is blocked (NFR-06) |
| 22 | `pip-licenses --format=json --with-system` | Every dependency's license ∈ allowlist (NFR-07) |
| 23 | `bandit -r 03-development/src/` | 0 HIGH, 0 MEDIUM |
| 24 | `mutmut run` then `mutmut results` | mutation score ≥ 70 (NFR-08) |
| 25 | Service shutdown with in-flight tasks | graceful drain; over-budget tasks marked `interrupted`; no orphan processes (FR-08) |
| 26 | `grep -c "^TASKQ_" .env.example` | 12 (all of §5.1 declared) |
| 27 | `make verify-system` | exit 0 and stdout contains `verify-system: PASS` (NFR-12) |

---

## 6. Out-of-Scope

The following items are explicitly out of scope for round 2 (`taskq-api`):

- **TypeScript service** — round 3 of the progressive test-bed, deferred.
- **CLI tool (`taskq-plus`)** — round 1, already delivered.
- **Horizontal-scaling implementation details** beyond the per-token token
  bucket and `pool_pre_ping` (constraints listed in `SPEC.md` §2 are the
  fidelity ceiling for this round).
- **Multi-tenant API key scoping beyond `read` < `write` < `admin`** — the
  three-tier hierarchy is the entire scope.
- **Distributed task scheduling** — execution is local-process via
  `asyncio.create_subprocess_exec`; a separate worker fleet is not in scope.
- **Webhook delivery / push notifications on task completion** — clients
  poll `GET /v1/tasks/{id}/runs`.

---

## 7. Open Issues / Deferred Items

The brief explicitly states that the §5.3 project-side config files are
**not optional** — their absence silently turns the corresponding NFRs into
free points. Items deferred to Phase 2+ (created during phase planning, not
omitted) are:

- **Deferred**: `requirements-dev.txt` content (the lock file plus
  `import-linter` / `pip-licenses` / `mutmut` / `pytest-benchmark` / `httpx`
  pins). The list of tools is in `SPEC.md` §5.3; concrete versions are
  chosen in Phase 2.
- **Deferred**: `Makefile` `verify-system` exact recipe — the chain is
  fixed by NFR-12, but the per-step `make` invocations are written in
  `03-development/` Phase 2.
- **Deferred**: `alembic.ini` url plumbing — the `TASKQ_DB_URL` indirection
  specified in `SPEC.md` §5.1 is fixed; the concrete `alembic.ini` is
  authored in Phase 2.
- **NFR-99 (Ambiguity-resolution tracker)**: see entries below. Each naming
  a canonical line that uses ambiguous phrasing; resolution is owned by the
  test harness, not by Agent A.

### NFR-99 entries

- **NFR-99-01**: `SPEC.md` §3 FR-05 says "per-token 令牌桶:容量
  `TASKQ_RATE_BURST`,補充速率 `TASKQ_RATE_PER_SEC`" and "row-level lock" —
  the exact row-level lock mechanism (e.g. `SELECT ... FOR UPDATE` vs
  SQLAlchemy `with_for_update`) is owned by the implementation phase per
  the canonical interpretation; measurement boundary is the rate-limit
  integration test from `SPEC.md` §8 #9.
- **NFR-99-02**: `SPEC.md` §4 NFR-01 names "constant SQL statement count" as
  the N+1 acceptance condition without specifying the exact listener
  instrument; the test harness owns the verification mechanism as long as
  the count is observably constant.
- **NFR-99-03**: `SPEC.md` §3 FR-09 says "/readyz returns 503 when ... or
  when `alembic current` is not at head" — the exact mechanism used to
  query `alembic current` (subprocess vs DB introspection) is implementation
  detail; the acceptance is the 503 + body that names the failure.

No additional open issues are outstanding at this time. Any future
discoveries surface in `08-risk/` rather than blocking this SRS.

---

## 8. Risks

The risk matrix is verbatim from `SPEC.md` §9. The mitigation column ties
each risk to the FR/NFR clause that prevents it.

| ID | Risk | Impact | Likelihood | Mitigation | Tied clauses |
|----|------|--------|------------|------------|--------------|
| R1 | **v3 data migration loses data** | High | Medium | Round-trip test against a real DB, column-by-column | FR-07, §8 #12 |
| R2 | SQL injection | High | Low | No string concatenation + ORM/parameterised + grep gate | NFR-02 |
| R3 | API key leak | High | Medium | Hashed storage + constant-time compare + printed once | FR-03 |
| R4 | 403 reveals resource existence | Medium | Medium | Authorise before lookup | FR-04, §8 #6 |
| R5 | N+1 collapses on a large table | High | High | Explicit eager loading + SQL count assertion | NFR-01, §8 #14 |
| R6 | Error body leaks internals | Medium | High | Fixed RFC 7807 fields + `detail` allowlist | FR-10 |
| R7 | **Swallowed `CancelledError` hangs shutdown** | Medium | Medium | Explicit ban + test assertion | NFR-03 |
| R8 | Timeout leaves orphan processes | Medium | Medium | `kill()` + `await wait()` | FR-08, §8 #25 |
| R9 | Deploy without migration | High | Medium | `/readyz` fails closed | FR-09, §8 #11 |
| R10 | Connection pool exhaustion | Medium | Medium | `pool_pre_ping` + concurrency cap | FR-06/08 |
| R11 | Transitive dep with incompatible license | Medium | Medium | Lock file + whole-tree scan | NFR-07 |
| R12 | Rate bucket race over-admits | Low | Medium | Single transaction + row-level lock | FR-05 |

Additional risks surfaced in this SRS (not in `SPEC.md` §9):

- **R13** — Async scanners in the framework have only ever faced synchronous
  code; any misjudgement they make on `async def` is itself a finding this
  test-bed is meant to surface. Recorded for Phase 4 bug hunt rather than
  worked around silently. Source: `PROJECT_BRIEF.md` "Phase 1 workflow rules".

---

## 9. Glossary

| Term | Definition |
|------|------------|
| ASGI | Asynchronous Server Gateway Interface; the interface served by FastAPI / uvicorn here. |
| `alembic current` | The Alembic command returning the revision label currently applied to the database. |
| `alembic upgrade head` / `alembic downgrade base` | Migrate forward to the latest revision / backward to the empty database. |
| API key | An opaque token presented via `X-API-Key`; its SHA-256 hash is stored in `api_keys`. |
| Bearer regex | Backwards-compatible shorthand for `Bearer\s+\S+`; see NFR-04. |
| Cursor pagination | Paging via an opaque cursor; offset-based paging is explicitly forbidden (FR-01). |
| `CancelledError` | `asyncio.CancelledError`; must propagate (NFR-03, FR-08). |
| Dependency (FastAPI) | A function whose return value is injected into a route handler; the single auth/authz decision point lives in `api/deps.py` (FR-04). |
| `detail` | The RFC 7807 field explaining the error; the value must not contain stack, SQL, or path (FR-10, NFR-02). |
| `hmac.compare_digest` | Constant-time comparison used for API key hashes (FR-03, NFR-02). |
| `import-linter` | The tool that enforces `.importlinter` layer contracts (NFR-06). |
| JSON Web Token (JWT) | **Not** used in this round; the project authenticates via `X-API-Key`. |
| Layer | One of `api > service > repository > models`; `config`/`errors` are independence (NFR-06). |
| MI | Maintainability Index, LLOC-weighted (NFR-11). |
| Mutation score | Percentage of mutants killed by the test suite (NFR-08). |
| N+1 | Anti-pattern where a list endpoint issues one query per row; explicit acceptance failure (NFR-01, FR-06). |
| ORM | Object-Relational Mapper; SQLAlchemy 2.x here. |
| `pool_pre_ping` | SQLAlchemy engine flag that issues a lightweight `SELECT 1` before each connection check-out (FR-06). |
| Problem+json | `application/problem+json` per RFC 7807 (FR-10). |
| Rate-limit bucket | Per-token token bucket persisted in `rate_buckets` (FR-05). |
| `Retry-After` | HTTP response header carrying the rate-limit cooldown (FR-05). |
| Round-trip | `upgrade head → write sample → downgrade -1 → upgrade head`; the FR-07 acceptance test. |
| Scope | `read` < `write` < `admin`; hierarchical containment (FR-04). |
| SBOM | Software Bill of Materials emitted at `08-config/SBOM.json` (NFR-07). |
| SHA-256 | Hash used for the API key `key_hash` column (FR-03, NFR-02). |
| `shell=True` | Forbidden flag of `subprocess`; see NFR-02, §8 #16. |
| `TaskGroup` | `asyncio.TaskGroup`; the background execution manager (FR-08). |
| Tenant | Out of scope for this round; the project is single-tenant per row in `api_keys`. |
| Tier (1/2/3) | The evaluation tier of a `dimension` per `evaluate_dimension.md`: Tier 1 = tool-scored gate, Tier 2 = tool-scored, Tier 3 = proxy-scored. |
| Token bucket | The rate-limit algorithm used in FR-05. |
| `verify-system` | The Makefile target that runs the migration + tests + smoke + migration round-trip (NFR-12). |

---

## 10. FR / NFR Block (machine-readable)

```json
{
  "version": "1.0",
  "created_at": "2026-08-07",
  "phase": 1,
  "project": "taskq-api",
  "canonical_spec": "SPEC.md",
  "canonical_spec_version": "v1.0.0",
  "functional_requirements": [
    {
      "id": "FR-01",
      "title": "Task Resource CRUD API",
      "description": "POST/GET/LIST/DELETE /v1/tasks, cursor pagination, 422/404/409",
      "implementation_functions": ["taskq_api.service.tasks", "taskq_api.api.tasks"],
      "verification_method": "integration test via httpx.ASGITransport (SPEC.md §8 #4, #5, #7, #8, #14)",
      "source": "SPEC.md §3 FR-01"
    },
    {
      "id": "FR-02",
      "title": "Task Execution Endpoint",
      "description": "POST /v1/tasks/{id}/run → 202; async subprocess; run history",
      "implementation_functions": ["taskq_api.service.runner", "taskq_api.api.tasks"],
      "verification_method": "integration test (SPEC.md §8 #25) + subprocess-orphan test",
      "source": "SPEC.md §3 FR-02"
    },
    {
      "id": "FR-03",
      "title": "API Key Authentication",
      "description": "X-API-Key, SHA-256 hashed, hmac.compare_digest, revocation",
      "implementation_functions": ["taskq_api.service.auth", "taskq_api.repository.key_repo"],
      "verification_method": "integration test (SPEC.md §8 #5, #18)",
      "source": "SPEC.md §3 FR-03"
    },
    {
      "id": "FR-04",
      "title": "Scope Authorisation",
      "description": "read < write < admin, single dependency, 403 leaks nothing",
      "implementation_functions": ["taskq_api.api.deps"],
      "verification_method": "integration test (SPEC.md §8 #6) + route-coverage assertion",
      "source": "SPEC.md §3 FR-04"
    },
    {
      "id": "FR-05",
      "title": "Rate Limiting",
      "description": "per-token token bucket in DB, 429 + Retry-After",
      "implementation_functions": ["taskq_api.service.ratelimit", "taskq_api.repository.rate_repo"],
      "verification_method": "integration test (SPEC.md §8 #9)",
      "source": "SPEC.md §3 FR-05"
    },
    {
      "id": "FR-06",
      "title": "Persistence Layer and Transaction Boundaries",
      "description": "repository layer, one Session per request, no raw SQL, no N+1",
      "implementation_functions": ["taskq_api.repository.session", "taskq_api.repository.task_repo"],
      "verification_method": "integration test (SPEC.md §8 #14, #17) + SQLAlchemy event listener count",
      "source": "SPEC.md §3 FR-06"
    },
    {
      "id": "FR-07",
      "title": "Schema Migration",
      "description": "Alembic v1→v2→v3, v3 moves data, every step reversible",
      "implementation_functions": ["migrations/versions/v1_initial", "migrations/versions/v2_tags", "migrations/versions/v3_split_results"],
      "verification_method": "real SQLite DB round-trip test (SPEC.md §8 #12, #13)",
      "source": "SPEC.md §3 FR-07"
    },
    {
      "id": "FR-08",
      "title": "Asynchronous Executor",
      "description": "asyncio.TaskGroup, concurrency cap, graceful drain, no orphans",
      "implementation_functions": ["taskq_api.service.runner"],
      "verification_method": "integration test (SPEC.md §8 #25) + CancelledError-propagation test",
      "source": "SPEC.md §3 FR-08"
    },
    {
      "id": "FR-09",
      "title": "Health Checks and Observability",
      "description": "/healthz, /readyz (fail-closed on migration lag), /v1/metrics",
      "implementation_functions": ["taskq_api.api.health"],
      "verification_method": "integration test (SPEC.md §8 #10, #11)",
      "source": "SPEC.md §3 FR-09"
    },
    {
      "id": "FR-10",
      "title": "Error Contract (RFC 7807)",
      "description": "application/problem+json + X-Correlation-Id",
      "implementation_functions": ["taskq_api.errors"],
      "verification_method": "integration test (SPEC.md §8 #19) + body-content assertion",
      "source": "SPEC.md §3 FR-10"
    }
  ],
  "non_functional_requirements": [
    {
      "id": "NFR-01",
      "type": "performance",
      "dimension": "performance",
      "description": "GET /v1/tasks/{id} p95 < 30ms; GET /v1/tasks?limit=50 p95 < 80ms (10k rows); constant SQL statement count",
      "test_method": "pytest-benchmark (SPEC.md §8 #14, #15)",
      "source": "SPEC.md §4 NFR-01"
    },
    {
      "id": "NFR-02",
      "type": "security",
      "dimension": "security",
      "description": "no shell=True/eval/exec; no string-concatenated SQL; hashed keys + constant-time compare; 403 leaks nothing; CORS deny-by-default; 0 HIGH/MEDIUM bandit",
      "test_method": "grep + bandit + integration (SPEC.md §8 #16, #17, #19, #23)",
      "source": "SPEC.md §4 NFR-02"
    },
    {
      "id": "NFR-03",
      "type": "reliability",
      "dimension": "error_handling",
      "description": "explicit transaction boundaries; no bare except; CancelledError must propagate; timeouts kill children; failed migration rolls back",
      "test_method": "ast-error-handling + integration (SPEC.md §8 #25)",
      "source": "SPEC.md §4 NFR-03"
    },
    {
      "id": "NFR-04",
      "type": "security",
      "dimension": "security",
      "description": "stdout/stderr/log/error redaction incl. DB URL password; key plaintext printed once",
      "test_method": "unit test for redaction + log-scan (SPEC.md §8 #20)",
      "source": "SPEC.md §4 NFR-04"
    },
    {
      "id": "NFR-05",
      "type": "documentation",
      "dimension": "documentation",
      "description": "100% public docstrings with [FR-XX]/[NFR-XX]; every endpoint in /openapi.json",
      "test_method": "ast-docstrings + openapi.json assertion",
      "source": "SPEC.md §4 NFR-05"
    },
    {
      "id": "NFR-06",
      "type": "layering",
      "dimension": "architecture_constraints",
      "description": "mandatory .importlinter layers contract + sqlalchemy forbidden contract",
      "test_method": "lint-imports (SPEC.md §8 #21)",
      "source": "SPEC.md §4 NFR-06"
    },
    {
      "id": "NFR-07",
      "type": "licensing",
      "dimension": "license_compliance",
      "description": "== pinning + requirements.lock; allowlist; scan whole tree; SBOM at 08-config/SBOM.json",
      "test_method": "pip-licenses --format=json --with-system (SPEC.md §8 #22)",
      "source": "SPEC.md §4 NFR-07"
    },
    {
      "id": "NFR-08",
      "type": "mutation",
      "dimension": "mutation_testing",
      "description": "features.mutation_testing: true; score ≥ 70 over service/ + repository/",
      "test_method": "mutmut run + results (SPEC.md §8 #24)",
      "source": "SPEC.md §4 NFR-08"
    },
    {
      "id": "NFR-09",
      "type": "testability",
      "dimension": "test_assertion_quality",
      "description": "0 skipped, 0 assertion-free, anti-fabrication, migration must be tested against a real DB file",
      "test_method": "pytest -q (SPEC.md §8 #1) + ast-assertions",
      "source": "SPEC.md §4 NFR-09"
    },
    {
      "id": "NFR-10",
      "type": "integration",
      "dimension": "integration_coverage",
      "description": "≥ 80% integration coverage via httpx.ASGITransport covering every error code",
      "test_method": "pytest-cov-integration (SPEC.md §8 #3)",
      "source": "SPEC.md §4 NFR-10"
    },
    {
      "id": "NFR-11",
      "type": "maintainability",
      "dimension": "readability",
      "description": "MI ≥ 80; CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines per API handler",
      "test_method": "readability-v2",
      "source": "SPEC.md §4 NFR-11"
    },
    {
      "id": "NFR-12",
      "type": "verifiability",
      "dimension": "execute_verification_target",
      "description": "make verify-system: migrate → tests → health smoke → migration round-trip, exit 0 with verify-system: PASS",
      "test_method": "make verify-system (SPEC.md §8 #27)",
      "source": "SPEC.md §4 NFR-12"
    }
  ]
}
```
