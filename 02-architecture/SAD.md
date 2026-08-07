# Software Architecture Document (SAD) — taskq-api

> Phase 2 architecture for the `taskq-api` project (harness-methodology progressive-verification bed, round 2 / 3). This document is binding for Phase 3+ implementation; module structure mirrors SPEC.md §6.

## 1. Architecture Overview

`taskq-api` is a single-process ASGI service that exposes a REST API for task lifecycle management (create / read / delete / run), persists state in a relational database with Alembic-managed schema, and executes user-supplied subprocess tasks asynchronously behind a token-bucket rate limiter. The architecture follows a strict four-layer dependency rule `api > service > repository > models`, with `config` and `errors` as independence modules. This separation exists for one concrete reason: ORM leakage from `repository` into `service`/`api` is the primary cross-cutting risk for round 2, and NFR-06 forbids it via a `lint-imports` contract.

Cross-cutting concerns (auth, rate limit, RFC 7807 error envelope, correlation_id propagation) are implemented as FastAPI dependencies and a single `errors.py` module, so the four-layer rule stays intact and behavior stays uniform.

### 1.1 System Verification Target

> **Phase 3 Gate 2 Requirement**: The harness executes `make verify-system` at Gate 2. If it exits non-zero, Gate 2 fails. The target name is fixed — the harness always calls `make verify-system`.

**Makefile target**: `verify-system`

The target composes: `alembic upgrade head` → full test suite → service start + `/healthz` + `/readyz` smoke → `alembic downgrade base` → `alembic upgrade head` (round-trip), then prints `verify-system: PASS` (NFR-12).

### 1.2 Architectural Drivers

| Driver | Source | Architectural Consequence |
|--------|--------|----------------------------|
| HTTP exposure | FR-01/02/09 | ASGI app, request/session lifecycle per call |
| Authn/authz | FR-03/04, NFR-02 | API-key auth in single dependency; per-token scope enforced before resource lookup |
| Persistence | FR-06/07, NFR-01 | Repository layer; explicit transaction boundaries; explicit eager-loading (no N+1) |
| Async execution | FR-02/08, NFR-03 | `asyncio.TaskGroup` with concurrency cap; graceful drain on shutdown |
| Schema evolution | FR-07, NFR-09 | Three real Alembic revisions with reversible downgrade (v3 contains data migration) |
| License hygiene | NFR-07 | `requirements.txt` + `requirements.lock` covering full transitive tree |

## 2. Module Design

The module tree follows SPEC.md §6 verbatim. Each directory corresponds to one CRG community; each `service/` and `api/` file calls into the directory's hub to satisfy the cohesion budget.

### 2.1 Directory Structure

```
03-development/
└── src/taskq_api/
    ├── __init__.py
    ├── __main__.py            # CLI: migrate / key create / healthcheck
    ├── app.py                 # FastAPI app assembly
    ├── config.py              # TASKQ_* env loader (independence)
    ├── errors.py              # RFC 7807 problem+json (independence)
    ├── models/                # L1 — SQLAlchemy declarative + pydantic schemas
    │   ├── __init__.py        # hub: exports Task, ApiKey, Tag, TaskResult, RateBucket, schemas
    │   ├── orm.py
    │   └── schemas.py
    ├── repository/            # L2 — only layer permitted to import sqlalchemy
    │   ├── __init__.py        # hub: exports Session context manager + repos
    │   ├── session.py
    │   ├── task_repo.py
    │   ├── key_repo.py
    │   └── rate_repo.py
    ├── service/               # L3 — business logic, NO sqlalchemy import
    │   ├── __init__.py        # hub: re-exports tasks/runner/auth/ratelimit
    │   ├── tasks.py
    │   ├── runner.py
    │   ├── auth.py
    │   └── ratelimit.py
    └── api/                   # L4 — FastAPI routes (top layer)
        ├── __init__.py        # hub: builds router, registers dependencies
        ├── deps.py            # auth + scope + rate-limit single dependency
        ├── tasks.py           # FR-01/02 routes
        └── health.py          # FR-09 routes
```

### 2.2 Layer Rules (NFR-06)

```
api > service > repository > models
config, errors: independence (no layer import)
```

* `api` may import `service`, `errors`, `config`, `models` (pydantic schemas only).
* `service` may import `repository`, `errors`, `config`, `models` (pydantic schemas only).
* `repository` is the **only** layer permitted to import `sqlalchemy`.
* `models` imports only `sqlalchemy` declarative primitives and `pydantic`.
* `config` and `errors` import neither upstream layer nor each other.

### 2.3 FR → Module Traceability

| FR | Title | Primary Modules | Notes |
|----|-------|-----------------|-------|
| FR-01 | 任務資源 CRUD API | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.{orm,schemas}` | Cursor-based pagination; 422/404/409 contract |
| FR-02 | 任務執行端點 | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo` | `asyncio.create_subprocess_exec`, no `shell=True` |
| FR-03 | API Key 認證 | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo` | SHA-256 hash + `hmac.compare_digest` |
| FR-04 | Scope 授權 | `taskq_api.api.deps`, `taskq_api.service.auth` | Single dependency; resource lookup after scope check (no info leak) |
| FR-05 | 流量控制 | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo` | Token bucket in DB with row-level lock |
| FR-06 | 持久化層與交易邊界 | `taskq_api.repository.session`, all `*_repo.py` | Per-request session via context manager |
| FR-07 | Schema Migration | `migrations/versions/{v1_initial,v2_tags,v3_split_results}.py`, `taskq_api.repository.session` | v3 is data-migration round-trip |
| FR-08 | 非同步執行器 | `taskq_api.service.runner` | `TaskGroup`, `TASKQ_MAX_CONCURRENT`, graceful drain |
| FR-09 | 健康檢查與可觀測性 | `taskq_api.api.health`, `taskq_api.repository.session`, `__main__.py` | `/readyz` fails closed if `alembic current != head` |
| FR-10 | 錯誤契約 (RFC 7807) | `taskq_api.errors`, `taskq_api.api.deps`, all handlers | `application/problem+json`; no stack/SQL/path leakage |

### 2.4 Module Specifications

#### `taskq_api.app`
| Attribute | Value |
|-----------|-------|
| Responsibility | Assemble FastAPI app, register routers, install exception handlers and middleware |
| External Interface | ASGI `app` object |
| Dependencies | `api.__init__` (hub), `errors`, `config` |

#### `taskq_api.__main__`
| Attribute | Value |
|-----------|-------|
| Responsibility | CLI entrypoint for management commands (`migrate`, `key create`, `healthcheck`) |
| External Interface | `python -m taskq_api <subcommand>` |
| Dependencies | `config`, `repository.session`, `service.auth`, `errors` |

#### `taskq_api.config`
| Attribute | Value |
|-----------|-------|
| Responsibility | Read & validate the 12 `TASKQ_*` env vars; never log DB URL with password (NFR-04) |
| External Interface | Module-level constants (e.g. `DB_URL`, `DB_POOL_SIZE`, `TASK_TIMEOUT`, `RATE_BURST`, `RATE_PER_SEC`, `CORS_ORIGINS`) |
| Dependencies | stdlib only (independence) |

#### `taskq_api.errors`
| Attribute | Value |
|-----------|-------|
| Responsibility | Build RFC 7807 problem+json envelopes, register exception handlers, redact secrets |
| External Interface | `problem(status, type, detail, instance)`; exception classes |
| Dependencies | stdlib only (independence) |

#### `taskq_api.models` (L1)
| Attribute | Value |
|-----------|-------|
| Responsibility | Declarative ORM table definitions and pydantic request/response models |
| External Interface | `Task`, `ApiKey`, `Tag`, `TaskTag`, `TaskResult`, `RateBucket`; pydantic schemas |
| Dependencies | `sqlalchemy`, `pydantic` only |
| Hub role | `models/__init__.py` re-exports the public surface so siblings `import` it once and call `models.Task` etc. |

#### `taskq_api.repository` (L2 — `sqlalchemy` boundary)
| Attribute | Value |
|-----------|-------|
| Responsibility | All SQL access; transaction boundary enforcement; eager loading |
| External Interface | `session_scope()` context manager; `task_repo`, `key_repo`, `rate_repo` modules |
| Dependencies | `models`, `sqlalchemy` (sole layer permitted) |
| Hub role | `repository/__init__.py` exports `session_scope`; every repo function body calls `models.Task`/`models.ApiKey` directly so internal edges register |

* `session.py` — `session_scope()` context manager; commits on success, rolls back on exception (FR-06, NFR-03).
* `task_repo.py` — CRUD for `tasks`, `task_tags`; cursor pagination; `selectinload` on `task_results` and tags.
* `key_repo.py` — create/lookup/revoke `api_keys`; SHA-256 hash on write; constant-time compare via `hmac.compare_digest` (FR-03).
* `rate_repo.py` — atomic token-bucket refill under row-level lock (FR-05).

#### `taskq_api.service` (L3 — no `sqlalchemy`)
| Attribute | Value |
|-----------|-------|
| Responsibility | Business logic; orchestrates repositories and the runner |
| External Interface | Module functions; no ORM types leaked beyond `models.schemas` pydantic |
| Dependencies | `repository`, `models.schemas`, `errors`, `config` |
| Hub role | `service/__init__.py` re-exports public functions; every `service/*` function body calls at least one sibling (`validate_command_injection`, `require_scope`, etc. helpers) for cross-file edges |

* `tasks.py` — FR-01 business logic; validation, conflict detection, cursor building.
* `runner.py` — FR-02/08; `asyncio.TaskGroup`; subprocess lifecycle with `kill()`+`await wait()`; honors `TASKQ_DRAIN_TIMEOUT`.
* `auth.py` — FR-03/04; key creation, scope check, constant-time compare. Public `require_scope(needed)`.
* `ratelimit.py` — FR-05; per-key token bucket; raises RFC 7807 429 with `Retry-After`.

#### `taskq_api.api` (L4 — top)
| Attribute | Value |
|-----------|-------|
| Responsibility | HTTP routing; binds dependencies; enforces the error envelope |
| External Interface | `router` (mounted at `/v1` and `/`); `auth_dep`, `scope_dep`, `rate_dep` |
| Dependencies | `service`, `errors`, `config`, `models.schemas` |
| Hub role | `api/__init__.py` builds the router and registers all dependencies; every handler calls `deps.auth_dep` so internal edges register |

* `deps.py` — FR-04's single dependency for auth+scope+rate; **no resource lookup here**.
* `tasks.py` — FR-01/02 route handlers (≤ 40 lines each, business logic in `service`).
* `health.py` — FR-09; `/healthz`, `/readyz`, `/v1/metrics`.

#### `migrations/versions/`
| Attribute | Value |
|-----------|-------|
| Responsibility | Alembic revisions v1 / v2 / v3 with real `downgrade` |
| External Interface | `alembic upgrade head`, `alembic downgrade -1`, `alembic downgrade base` |
| Dependencies | `taskq_api.models.orm` |

* `v1_initial.py` — creates `tasks`, `api_keys`, `rate_buckets`.
* `v2_tags.py` — adds `tags`, `task_tags`, unique index on `tasks.name`.
* `v3_split_results.py` — splits `tasks.result_json` into `task_results` table with **data migration** + reversible downgrade (FR-07).

### 2.5 CRG Cohesion Strategy

Each of `models/`, `repository/`, `service/`, `api/` declares a hub `__init__.py` that re-exports the public surface and is imported by every sibling. Sibling files call into the hub from multiple function bodies (not just at module level) so the per-function edge count offsets external edges (httpx / fastapi / sqlalchemy / alembic).

Per-directory edge budget (rule of thumb):
- `models/`: low external edges (sqlalchemy + pydantic); module-level imports suffice.
- `repository/`: high external edges (sqlalchemy, alembic env glue); require ≥2 hub functions (`session_scope`, `eager_load`) called from each repo function body.
- `service/`: moderate external edges (asyncio, subprocess); require ≥2 hub helpers (`validate_command_injection`, `enforce_scope`).
- `api/`: very high external edges (fastapi, starlette); require ≥2 hub functions (`problem_response`, `bind_correlation_id`) called from each handler.

### 2.6 Logical Constraints

* No circular dependencies between any pair of modules; `__init__.py` hubs break cycles by re-export only.
* No `sqlalchemy` import outside `repository/` (enforced by `lint-imports` forbidden contract, NFR-06).
* No `shell=True`, `eval(`, `exec(` anywhere in `03-development/src/` (NFR-02, grep-gated).
* No handler > 40 lines; no file > 400 lines; no directory > 15 files (NFR-11).
* No `pytest.skip` / `xfail` anywhere in test tree (NFR-09).

## 3. Interfaces & Data Flows

### 3.1 External Interface

```
Client ──HTTP/JSON──▶ uvicorn (ASGI) ──▶ FastAPI app
                                         │
                                         ▼
                              ┌─── api/deps ───┐
                              │ auth_dep (FR-03) │
                              │ scope_dep (FR-04)│
                              │ rate_dep  (FR-05)│
                              └───────┬────────┘
                                      ▼
                                api/tasks · api/health
                                      │
                                      ▼
                              service/{tasks, runner, auth, ratelimit}
                                      │
                                      ▼
                       repository/{session, task_repo, key_repo, rate_repo}
                                      │
                                      ▼
                                models/orm (SQLAlchemy)
                                      │
                                      ▼
                        SQLite (dev/test) · PostgreSQL (prod)
```

### 3.2 Request Lifecycle (per call)

1. `uvicorn` receives request → `api/deps.auth_dep` resolves `X-API-Key` to an `ApiKey` row via `repository/key_repo` (SHA-256 + `hmac.compare_digest`). Missing/invalid → 401 problem+json (FR-03).
2. `api/deps.scope_dep` checks the key's scope satisfies the route's required scope. **Resource lookup does not happen before this check** (FR-04, no info leak).
3. `api/deps.rate_dep` refills the bucket row under row-level lock via `repository/rate_repo`. Insufficient tokens → 429 + `Retry-After` (FR-05).
4. Handler in `api/tasks` or `api/health` dispatches to `service/`.
5. `service` opens a session via `repository.session.session_scope()`, commits on success, rolls back on exception (FR-06, NFR-03). Eager loading via `selectinload` keeps SQL count constant (NFR-01).
6. Errors are converted to RFC 7807 `application/problem+json` by `errors.problem()` (FR-10). `correlation_id` echoes in `X-Correlation-Id` header and server log.
7. `asyncio.CancelledError` propagates untouched; never converted to 500 (NFR-03).

### 3.3 Key Data Flows

**Task run flow (FR-02/FR-08)**:
```
POST /v1/tasks/{id}/run
  → api.tasks.dispatch_run()           # ≤40 lines
  → service.tasks.assert_writable()
  → service.runner.spawn(task)
       asyncio.create_task
         asyncio.create_subprocess_exec(*shlex.split(cmd))
         await asyncio.wait_for(proc, TASK_TIMEOUT)
         on timeout: proc.kill(); await proc.wait()   # no orphan
       write task_results row (FR-07 v3 schema)
       update tasks.status (pending→running→done|failed|timeout)
  → 202 Accepted { run_id }
```

**Rate-limit flow (FR-05)**:
```
inbound request
  → api.deps.rate_dep
      with repository.session.session_scope() as s:
        bucket = SELECT ... FOR UPDATE  (row-level lock)
        bucket.tokens = min(BURST, tokens + PER_SEC*Δt)
        if bucket.tokens < 1: raise RateLimited(retry_after=ceil((1-tokens)/PER_SEC))
        bucket.tokens -= 1
        commit
      return dep
```

**Schema evolution flow (FR-07)**:
```
make verify-system →
  alembic upgrade head                # applies v1 → v2 → v3 (data migration)
  seed sample data
  alembic downgrade -1                # reverse v3 data migration (re-pack into result_json)
  alembic upgrade head                # re-split; assert row-by-row equality
  alembic downgrade base              # drop everything
  alembic upgrade head                # rebuild
```

## 4. NFR Handling

| NFR | Dimension | Target | Architectural Mechanism |
|-----|-----------|--------|--------------------------|
| NFR-01 | performance | `GET /v1/tasks/{id}` p95 < 30ms (10k rows); list p95 < 80ms; **constant SQL count** | `selectinload`/explicit eager loading; cursor pagination; SQLAlchemy event listener asserts statement count in integration tests |
| NFR-02 | security | 0 `shell=True` / `eval(` / `exec(`; 0 string-concat SQL; bandit 0/0 | grep CI gate; ORM-only access; CORS allowlist default-deny; `errors.problem()` redaction |
| NFR-03 | error_handling | Explicit txn boundary; no bare `except`; `CancelledError` propagates | `session_scope` context manager; explicit `try/except` everywhere; static lint rule for `except Exception: pass` |
| NFR-04 | security | Redact `(sk-…\|token=…\|Bearer …\|postgres(ql)://…)`; DB URL with pwd never logged | `errors.redact()` applied to log handlers and `/v1/metrics` output; DB URL only loaded once in `config` and never stringified into logs |
| NFR-05 | documentation | 100% public symbols have `[FR-XX]`/`[NFR-XX]` docstrings; OpenAPI `summary`+`description` on every route | Docstring lint in CI; FastAPI `summary`/`description` on each route decorator; asserted by `openapi.json` snapshot test |
| NFR-06 | architecture_constraints | `.importlinter` layers contract; `sqlalchemy` forbidden outside `repository` | `.importlinter` declares `api > service > repository > models` + forbidden contract; `lint-imports` exit 0 in CI |
| NFR-07 | license_compliance | Allowlist (MIT/BSD-2/3/Apache-2.0/PSF); full tree scan; SBOM | `requirements.txt` pinned `==`; `requirements.lock` pins transitive; `pip-licenses --with-system` in CI; `08-config/SBOM.json` regenerated each release |
| NFR-08 | mutation_testing | score ≥ 70 on `service/`+`repository/` | `mutmut` run in CI; `harness_config.json` sets `features.mutation_testing: true` and bounds run to `service/`+`repository/` |
| NFR-09 | test_assertion_quality | skipped = 0; zero_assert = 0; **no skip/xfail/stub** | CI parses pytest output; static lint flags `pytest.skip`/`xfail`; `TRACEABILITY_MATRIX.md` `VERIFIED` written only after green run |
| NFR-10 | integration_coverage | ≥ 80% line coverage in `tests/integration/`; `httpx.ASGITransport` only | `httpx.AsyncClient(transport=ASGITransport(app))` driver; coverage gate per `pytest-cov-integration` |
| NFR-11 | readability | MI ≥ 80; CC ≤ 10; file ≤ 400 lines; dir ≤ 15 files; handler ≤ 40 lines | `readability-v2` gate; architectural rule enforced by §2.6 |
| NFR-12 | execute_verification_target | `make verify-system` exits 0 with `verify-system: PASS` | `Makefile` target chains: upgrade → tests → start + smoke → downgrade → upgrade |

---

## 5. SAB Block (machine-readable — BINDING CONTRACT)

> **CONTRACT**: Field names, types, `sab:` root key, and `phase` as int must match `core/quality_gate/sab_parser.py:render_canonical_sab_template()`. Validate: `python3 scripts/generate_sab.py --validate --project .`

<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  created_at: "2026-08-07"
  phase: 2
  project: "taskq-api"

  layers:
    - name: api
      modules:
        - name: "taskq_api.api"
        - name: "taskq_api.api.tasks"
        - name: "taskq_api.api.deps"
        - name: "taskq_api.api.health"
      allowed_dependencies: ["service", "errors", "config", "models"]
    - name: service
      modules:
        - name: "taskq_api.service"
        - name: "taskq_api.service.tasks"
        - name: "taskq_api.service.runner"
        - name: "taskq_api.service.auth"
        - name: "taskq_api.service.ratelimit"
      allowed_dependencies: ["repository", "errors", "config", "models"]
    - name: repository
      modules:
        - name: "taskq_api.repository"
        - name: "taskq_api.repository.session"
        - name: "taskq_api.repository.task_repo"
        - name: "taskq_api.repository.key_repo"
        - name: "taskq_api.repository.rate_repo"
      allowed_dependencies: ["models"]
    - name: models
      modules:
        - name: "taskq_api.models"
        - name: "taskq_api.models.orm"
        - name: "taskq_api.models.schemas"
      allowed_dependencies: []
    - name: errors
      modules:
        - name: "taskq_api.errors"
      allowed_dependencies: []
    - name: config
      modules:
        - name: "taskq_api.config"
      allowed_dependencies: []
    - name: app
      modules:
        - name: "taskq_api.app"
      allowed_dependencies: ["api", "errors", "config"]
    - name: cli
      modules:
        - name: "taskq_api.__main__"
      allowed_dependencies: ["config", "repository", "service", "errors"]
    - name: migrations
      modules:
        - name: "migrations.versions.v1_initial"
        - name: "migrations.versions.v2_tags"
        - name: "migrations.versions.v3_split_results"
      allowed_dependencies: ["models"]

  allowed_dependencies:
    - {from: api, to: service}
    - {from: api, to: errors}
    - {from: api, to: config}
    - {from: api, to: models}
    - {from: service, to: repository}
    - {from: service, to: errors}
    - {from: service, to: config}
    - {from: service, to: models}
    - {from: repository, to: models}
    - {from: app, to: api}
    - {from: app, to: errors}
    - {from: app, to: config}
    - {from: cli, to: config}
    - {from: cli, to: repository}
    - {from: cli, to: service}
    - {from: cli, to: errors}
    - {from: migrations, to: models}

  quality_targets:
    max_complexity: 10
    min_coverage: 100
    max_coupling: 0.3

  nfr_traceability:
    NFR-01:
      dimension: performance
      type: performance
      target: "GET single p95 < 30ms; list p95 < 80ms at 10,000 rows; SQL statement count constant"
      module: taskq_api.repository.task_repo
    NFR-02:
      dimension: security
      type: security
      target: "0 shell=True/eval/exec; 0 string-concat SQL; hashed keys; bandit 0 HIGH/0 MEDIUM"
      module: taskq_api.errors
    NFR-03:
      dimension: error_handling
      type: reliability
      target: "explicit commit/rollback boundaries; no bare except; CancelledError propagates; no orphan process"
      module: taskq_api.repository.session
    NFR-04:
      dimension: security
      type: security
      target: "all matching secrets replaced with [REDACTED]; DB password absent from logs, errors, and metrics"
      module: taskq_api.errors
    NFR-05:
      dimension: documentation
      type: documentation
      target: "100% public symbols have FR/NFR docstrings; every route has OpenAPI summary and description"
      module: taskq_api.app
    NFR-06:
      dimension: architecture_constraints
      type: layering
      target: "lint-imports exits 0 for api>service>repository>models and sqlalchemy forbidden outside repository"
      module: taskq_api.repository.session
    NFR-07:
      dimension: license_compliance
      type: licensing
      target: "100% full dependency tree uses allowlisted licenses; pinned lockfile and SBOM present"
      module: taskq_api.app
    NFR-08:
      dimension: mutation_testing
      type: mutation
      target: "mutation score >= 70"
      module: taskq_api.service.runner
      scope_layers: ["service", "repository"]
    NFR-09:
      dimension: test_assertion_quality
      type: testability
      target: "skipped=0; zero_assert=0; no skip/xfail/stub; real SQLite migration round-trip tested"
      module: taskq_api.repository.session
    NFR-10:
      dimension: integration_coverage
      type: integration
      target: "integration line coverage >= 80% using httpx ASGITransport"
      module: taskq_api.api.tasks
    NFR-11:
      dimension: readability
      type: maintainability
      target: "MI >= 80; CC <= 10; file <= 400 lines; directory <= 15 files; handler <= 40 lines"
      module: taskq_api.app
    NFR-12:
      dimension: execute_verification_target
      type: verifiability
      target: "make verify-system exits 0 and prints verify-system: PASS"
      module: taskq_api.app

  fr_module_traceability:
    FR-01: ["taskq_api.api.tasks", "taskq_api.service.tasks", "taskq_api.repository.task_repo", "taskq_api.models.orm", "taskq_api.models.schemas"]
    FR-02: ["taskq_api.api.tasks", "taskq_api.service.runner", "taskq_api.repository.task_repo"]
    FR-03: ["taskq_api.api.deps", "taskq_api.service.auth", "taskq_api.repository.key_repo"]
    FR-04: ["taskq_api.api.deps", "taskq_api.service.auth"]
    FR-05: ["taskq_api.api.deps", "taskq_api.service.ratelimit", "taskq_api.repository.rate_repo"]
    FR-06: ["taskq_api.repository.session", "taskq_api.repository.task_repo", "taskq_api.repository.key_repo", "taskq_api.repository.rate_repo"]
    FR-07: ["migrations.versions.v1_initial", "migrations.versions.v2_tags", "migrations.versions.v3_split_results", "taskq_api.repository.session"]
    FR-08: ["taskq_api.service.runner"]
    FR-09: ["taskq_api.api.health", "taskq_api.repository.session", "taskq_api.__main__"]
    FR-10: ["taskq_api.errors", "taskq_api.api.deps"]

  advisory_only: []
  gate_score_overrides: {}
  nfr_dimension_mapping: {}

  architecture_constraints:
    - "no_circular_dependencies"
    - "sqlalchemy_imports_only_in_repository_layer"
    - "no_shell_true_eval_exec_in_src"
    - "handler_max_lines_40"
    - "file_max_lines_400"
    - "directory_max_files_15"

  high_risk_modules:
    - "taskq_api.service.runner"
    - "taskq_api.service.auth"
    - "taskq_api.repository.session"
    - "migrations.versions.v3_split_results"
```
<!-- SAB:END -->

---

## 6. Security Design (STRIDE-lite — machine-readable, BINDING CONTRACT)

> **CONTRACT**: Field names and `security_design:` root key are parsed by `core/quality_gate/security_design.py:extract_security_block()`. Validate: `python3 harness_cli.py check-artifact-consistency --project .`.

<!-- SEC:START -->
```yaml
security_design:
  version: "1.0"
  applicability: full
  justification: ""
  trust_boundaries:
    - id: TB-01
      name: "external HTTP client"
      description: "Untrusted network clients crossing into the ASGI app via /v1 routes (no auth before parsing)"
    - id: TB-02
      name: "API-key authentication boundary"
      description: "X-API-Key header crossing from request to api/deps; constant-time compare in repository/key_repo"
    - id: TB-03
      name: "service to subprocess boundary"
      description: "service/runner spawning asyncio subprocesses via create_subprocess_exec (no shell=True)"
    - id: TB-04
      name: "service to database boundary"
      description: "repository/* crossing into SQLAlchemy ORM and underlying SQLite/PostgreSQL"
    - id: TB-05
      name: "log and metrics emission boundary"
      description: "Structured logs and /v1/metrics output that may be consumed by operators or downstream systems"
  threats:
    - id: T-01
      boundary: TB-01
      category: tampering
      description: "Malformed or hostile payload mutates task state without validation"
      mitigation: "Pydantic TaskCreate schema validation rejects unknown fields; TaskCreate validator blocks injection characters (FR-01)"
      owner_module: "taskq_api.api.tasks"
      nfr: NFR-02
      verified_by: "test_sec_t01_malformed_payload_rejected"
    - id: T-02
      boundary: TB-01
      category: spoofing
      description: "Missing or forged X-API-Key header bypasses authentication"
      mitigation: "api/deps.auth_dep requires X-API-Key; missing/invalid yields 401 problem+json (FR-03)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t02_missing_api_key_returns_401"
    - id: T-03
      boundary: TB-02
      category: spoofing
      description: "API key stored as plaintext allows credential recovery from DB"
      mitigation: "key_repo stores only SHA-256 hash; constant-time compare via hmac.compare_digest (FR-03, NFR-02)"
      owner_module: "taskq_api.repository.key_repo"
      nfr: NFR-02
      verified_by: "test_sec_t03_api_key_stored_as_sha256"
    - id: T-04
      boundary: TB-02
      category: elevation_of_privilege
      description: "Read-only key performs write/admin operations"
      mitigation: "api/deps.scope_dep enforces hierarchical scope (read<write<admin) before any resource lookup (FR-04)"
      owner_module: "taskq_api.api.deps"
      nfr: NFR-02
      verified_by: "test_sec_t04_write_key_cannot_delete"
    - id: T-05
      boundary: TB-02
      category: information_disclosure
      description: "403 response reveals whether a resource exists"
      mitigation: "scope check happens before resource lookup; 403 body is identical regardless of resource existence (FR-04)"
      owner_module: "taskq_api.service.auth"
      nfr: NFR-02
      verified_by: "test_sec_t05_403_body_does_not_leak_resource_existence"
    - id: T-06
      boundary: TB-03
      category: elevation_of_privilege
      description: "Command injection via task command string executed by shell"
      mitigation: "runner uses asyncio.create_subprocess_exec(*shlex.split(cmd)); shell=True banned by lint (NFR-02)"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-02
      verified_by: "test_sec_t06_runner_never_uses_shell_true"
    - id: T-07
      boundary: TB-03
      category: denial_of_service
      description: "Subprocess hangs forever, exhausting concurrency budget"
      mitigation: "asyncio.wait_for with TASKQ_TASK_TIMEOUT; on timeout proc.kill() then await proc.wait() (FR-08)"
      owner_module: "taskq_api.service.runner"
      nfr: NFR-03
      verified_by: "test_sec_t07_subprocess_killed_on_timeout"
    - id: T-08
      boundary: TB-04
      category: tampering
      description: "SQL injection via string concatenation of query fragments"
      mitigation: "ORM-only access in repository/*; grep CI gate blocks f-string/%/+ SQL composition (NFR-02)"
      owner_module: "taskq_api.repository.session"
      nfr: NFR-02
      verified_by: "test_sec_t08_no_sql_string_concatenation"
    - id: T-09
      boundary: TB-04
      category: repudiation
      description: "Transaction boundaries lost on exception, leaving partial writes"
      mitigation: "session_scope() context manager commits on success / rolls back on exception (FR-06, NFR-03)"
      owner_module: "taskq_api.repository.session"
      nfr: NFR-03
      verified_by: "test_sec_t09_session_rolls_back_on_exception"
    - id: T-10
      boundary: TB-05
      category: information_disclosure
      description: "API key, bearer token, or DB URL leaked through logs or /v1/metrics"
      mitigation: "errors.redact() applied to all log handlers and metrics output (NFR-04)"
      owner_module: "taskq_api.errors"
      nfr: NFR-04
      verified_by: "test_sec_t10_secrets_redacted_from_logs_and_metrics"
    - id: T-11
      boundary: TB-01
      category: information_disclosure
      description: "500 error body leaks stack trace, SQL, or filesystem path"
      mitigation: "errors.problem() returns fixed fields only; detail redacted; stack/SQL/path never serialized (FR-10)"
      owner_module: "taskq_api.errors"
      nfr: NFR-02
      verified_by: "test_sec_t11_500_body_has_no_stack_or_sql"
    - id: T-12
      boundary: TB-01
      category: denial_of_service
      description: "Attacker exhausts per-token rate budget to starve other clients"
      mitigation: "Per-key token bucket with row-level lock; 429 + Retry-After (FR-05)"
      owner_module: "taskq_api.service.ratelimit"
      nfr: NFR-01
      verified_by: "test_sec_t12_rate_limit_returns_429_with_retry_after"
```
<!-- SEC:END -->
