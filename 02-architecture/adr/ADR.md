# Architecture Decision Records (ADR) — taskq-api

> Phase 2 binding decisions for `taskq-api` (harness-methodology progressive-verification bed, round 2/3). Each ADR derives from SAD.md and is referenced by Phase 3+ implementation. Status reflects the current implementation state.

## Traceability Matrix

Every decision below exists to satisfy a requirement stated in the Software
Requirements Specification (`01-requirements/SRS.md`), which in turn transcribes
`SPEC.md`. This table is the forward index ADR → requirement: it names the FRs
and NFRs each decision was taken to serve, and the SRS section that states them.
The reverse direction (requirement → module → test) is maintained separately in
`01-requirements/TRACEABILITY_MATRIX.md` and is not duplicated here.

| ADR | Decision | FRs served | NFRs served | SRS / specification source |
|-----|----------|------------|-------------|----------------------------|
| ADR-001 | Four-layer strict dependency architecture | FR-06 | NFR-06, NFR-11 | SRS §4 NFR-06 AC-N06.1/.2 (`.importlinter` layers contract; `sqlalchemy` forbidden outside `repository/`); §4 NFR-11 AC-N11.4 (business logic lives in `service/`, handlers stay thin) |
| ADR-002 | Python 3.11 + FastAPI ASGI stack | FR-01, FR-02, FR-09 | NFR-05, NFR-10 | SRS §4 NFR-05 AC-N05.2 (`summary`/`description` per endpoint in the OpenAPI schema); §4 NFR-10 AC-N10.2 (integration driven via `httpx.AsyncClient(transport=ASGITransport(app))`) |
| ADR-003 | SQLAlchemy + Alembic persistence stack | FR-06, FR-07 | NFR-01, NFR-02 | SRS §4 NFR-01 AC-N01.3 (list-endpoint SQL statement count constant — N+1 guard); §4 NFR-02 AC-N02.2 (no string-concatenated SQL) |
| ADR-004 | `asyncio.TaskGroup` subprocess supervision | FR-02, FR-08 | NFR-02, NFR-03 | SRS §4 NFR-02 AC-N02.1 (`shell=True` / `eval(` / `exec(` absent from source); §4 NFR-03 AC-N03.3/.5 (`CancelledError` propagates; timeout leaves no orphan process) |
| ADR-005 | DB-backed token bucket under a row-level lock | FR-05 | NFR-03 | SRS §3 FR-05 AC-5.2 (bucket state persisted; update in one transaction with a row-level lock); §4 NFR-03 AC-N03.1 (explicit transaction boundaries) |
| ADR-006 | RFC 7807 `application/problem+json` envelope | FR-10 | NFR-02, NFR-04 | SRS §4 NFR-02 AC-N02.5 (no stack trace / SQL / path in error bodies); §4 NFR-04 AC-N04.1/.2 (redaction before emission; DB connection string never logged) |
| ADR-007 | SHA-256 key hash + `hmac.compare_digest` | FR-03, FR-04 | NFR-02, NFR-04 | SRS §4 NFR-02 AC-N02.3/.4 (hashed storage, constant-time compare; 403 hides resource existence); §4 NFR-04 AC-N04.3 (key plaintext printed exactly once) |
| ADR-008 | Alembic v3 data migration with a real downgrade | FR-07 | NFR-03, NFR-09 | SRS §4 NFR-03 AC-N03.6 (a failed migration rolls back to the previous revision); §4 NFR-09 AC-N09.5 (migration tested against a real SQLite database file, never downgraded to a skip) |
| ADR-009 | `session_scope()` transaction boundary | FR-06 | NFR-03 | SRS §4 NFR-03 AC-N03.1/.2 (commit on success / rollback on exception via a context manager; no bare `except`) |
| ADR-010 | Module hub `__init__.py` re-exports | — | NFR-11 | SAD §2.5 (per-directory CRG cohesion) is the driver; it interacts with SRS §4 NFR-11 AC-N11.3 (≤ 400 lines per file, ≤ 15 files per directory), whose size caps force many small files per layer — the hub is what keeps each layer's public surface stable across that split |
| ADR-011 | License allowlist + `requirements.lock` + SBOM | — | NFR-07 | SRS §4 NFR-07 AC-N07.1–.4 (`==` pins plus a fully pinned lock file, permissive-license allowlist, full direct+transitive scan, SBOM at `08-config/SBOM.json`) |
| ADR-012 | `make verify-system` as the system verification target | — | NFR-12 | SRS §4 NFR-12 AC-N12.1/.2 (the target chains migrate → tests → health smoke → downgrade/upgrade round-trip, exits 0 and prints `verify-system: PASS`) |
| ADR-013 | Cross-cutting quality NFRs enforced as tooling gates | — | NFR-05, NFR-08, NFR-09, NFR-11 | SRS §4 NFR-05 AC-N05.1, §4 NFR-08 AC-N08.1–.3, §4 NFR-09 AC-N09.1–.6, §4 NFR-11 AC-N11.1–.3 — whole-codebase properties with no single owning structural decision |

## ADR-001: Four-Layer Strict Dependency Architecture

### Status
Accepted

### Context
`taskq-api` is an ASGI service with persistence and async subprocess execution. Cross-cutting concerns (auth, rate limit, RFC 7807 envelope, correlation_id) must be uniform. The primary architectural risk is ORM leakage from `repository` into `service`/`api`, which couples business logic to a specific SQL toolkit and blocks test substitutions.

### Decision
Adopt a strict four-layer dependency rule:
- `api` may import `service`, `errors`, `config`, `models` (pydantic schemas only).
- `service` may import `repository`, `errors`, `config`, `models` (pydantic schemas only).
- `repository` is the only layer permitted to import `sqlalchemy`.
- `models` imports only `sqlalchemy` declarative primitives and `pydantic`.
- `config` and `errors` are independence modules importing neither upstream layer nor each other.

Enforce via `.importlinter` layers contract plus a `lint-imports` CI gate (NFR-06).

### Rationale
- One-way dependency chain is mechanically checkable; no circular-import risk.
- ORM substitution (e.g. `InMemoryRepo` in tests) is possible because no layer above `repository` knows `sqlalchemy` exists.
- The four modules map cleanly to CRG communities (`models/`, `repository/`, `service/`, `api/`) — one community per layer.

### Consequences
- Positive: Test substitution, clear ownership, lint-enforced contract.
- Negative: ORM types must be re-projected into pydantic at the `repository`/`models` boundary; small ceremony overhead.

### Alternatives Considered
- **Anemic three-layer (api > service > models with raw SQL in service)**: rejected — couples business logic to SQL; no substitution seam.
- **Hexagonal / Ports-and-Adapters**: rejected — overkill for round-2 scope; forces `repository` interface + adapter separation that pays off only with multiple DB backends.
- **Flat module with no layers**: rejected — violates NFR-06 `architecture_constraints`.

## ADR-002: Python 3.11 + FastAPI ASGI Stack

### Status
Accepted

### Context
The service exposes a REST API for task lifecycle management (FR-01/02/09) with per-request scope and rate-limit enforcement. The runtime must support async subprocess execution without GIL contention, pydantic schema validation, and OpenAPI generation.

### Decision
Use Python 3.11.15 as the runtime, FastAPI as the ASGI framework, uvicorn as the server, pydantic for request/response schemas, and httpx (with `ASGITransport`) as the integration-test driver. Alembic is invoked from a Makefile target, not embedded in the app process.

### Rationale
- Python 3.11 provides `asyncio.TaskGroup` (PEP 654) — the chosen pattern for FR-08 concurrent subprocess supervision.
- FastAPI integrates dependency injection with pydantic validation and auto-generates OpenAPI; satisfies NFR-05 documentation completeness.
- httpx + `ASGITransport` runs the app in-process without TCP bind; satisfies NFR-10 integration-coverage target without flaky ports.

### Consequences
- Positive: Native async, no GIL workaround, deterministic tests.
- Negative: Locked to Python 3.11+; FastAPI/Starlette surface must stay current.

### Alternatives Considered
- **Starlette + manual pydantic**: rejected — duplicates FastAPI's routing/dependency layer; no OpenAPI out of the box.
- **Flask + sync workers**: rejected — cannot host asyncio subprocess supervision cleanly; blocks FR-08.
- **Django + DRF**: rejected — admin/ORM bring surface area that NFR-06 forbids in `repository`.

## ADR-003: SQLAlchemy + Alembic Persistence Stack

### Status
Accepted

### Context
Tasks, API keys, tags, results, and rate-bucket state must persist durably with schema evolution. Three real Alembic revisions are required (FR-07); one of them (v3) splits a JSON column into a child table — i.e. a real data migration with reversible downgrade.

### Decision
Use SQLAlchemy declarative ORM (only inside `repository/`) and Alembic for migrations. SQLite for dev/test; PostgreSQL for production (driver selectable via `TASKQ_DB_URL`). `make verify-system` performs an upgrade → downgrade → upgrade round-trip to prove reversibility.

### Rationale
- SQLAlchemy's `selectinload` / `joinedload` enables explicit eager loading (NFR-01 constant SQL count).
- Alembic's revision DAG supports real `downgrade()`; a downgrade that just `pass`es would fail NFR-09 traceability.
- ORM-only access blocks string-concatenation SQL injection (T-08 mitigation; NFR-02 grep gate).

### Consequences
- Positive: Reversible schema, ORM-substitutable repos, CI-enforced migration round-trip.
- Negative: SQLite-vs-PostgreSQL dialect differences (e.g. `FOR UPDATE` only on PG) must be coded with dialect-aware fallback or restricted to PG in prod.

### Alternatives Considered
- **Raw SQL with hand-written migrations**: rejected — no type safety; blocks NFR-02 grep-gate for string-concat SQL.
- **Django ORM**: rejected — too tightly coupled to Django request cycle.
- **Peewee**: rejected — weaker async support, smaller community.

## ADR-004: asyncio.TaskGroup for Concurrent Subprocess Execution

### Status
Accepted

### Context
FR-02/08 require user-supplied subprocess commands to run asynchronously with bounded concurrency (`TASKQ_MAX_CONCURRENT`), per-task timeout (`TASKQ_TASK_TIMEOUT`), and graceful drain on shutdown. Naive `asyncio.gather` swallows the first exception and breaks supervision.

### Decision
Use `asyncio.TaskGroup` (Python 3.11+) to supervise concurrent subprocesses. Each spawned task uses `asyncio.create_subprocess_exec(*shlex.split(cmd))` (no `shell=True`). On `wait_for` timeout: `proc.kill()` followed by `await proc.wait()` to guarantee no orphan. On shutdown, `TaskGroup` drains within `TASKQ_DRAIN_TIMEOUT`.

### Rationale
- TaskGroup (PEP 654) cancels siblings on first failure and propagates `ExceptionGroup` — matches the "one bad task cancels cohort" semantic needed for FR-08.
- `create_subprocess_exec` with an argv list blocks shell injection (T-06 mitigation; NFR-02 grep gate).
- `proc.kill()` + `await proc.wait()` is the documented asyncio idiom for guaranteed cleanup.

### Consequences
- Positive: Bounded concurrency, deterministic failure mode, no orphan subprocesses.
- Negative: Python 3.11+ floor is now mandatory; older interpreters are excluded.

### Alternatives Considered
- **`ThreadPoolExecutor` per subprocess**: rejected — exposes GIL contention and lacks timeout guarantees; not natively supervised.
- **`asyncio.create_task` + manual cancel loop**: rejected — duplicates TaskGroup semantics with no upside.
- **`subprocess.Popen` from threads**: rejected — defeats the async supervision model entirely.

## ADR-005: Token-Bucket Rate Limiting with Row-Level Lock

### Status
Accepted

### Context
FR-05 requires per-API-key rate limiting with `Retry-After` semantics. The limit must hold across processes (multi-worker uvicorn) and across restarts — i.e. the bucket state must live in the DB, not in-process.

### Decision
Implement a per-key token bucket persisted in `rate_buckets`. Each request opens a session via `session_scope()` and refills/charges the bucket under `SELECT ... FOR UPDATE` (row-level lock). If `tokens < 1`, raise `RateLimited(retry_after=ceil((1-tokens)/RATE_PER_SEC))`; the FastAPI handler returns 429 `application/problem+json` with the `Retry-After` header.

### Rationale
- DB-backed bucket survives process restart and works across workers (no race because of the row-level lock).
- Atomicity is the actual decision; the token-bucket shape is conventional. `FOR UPDATE` serializes the read-modify-write.
- 429 + `Retry-After` is RFC 6585; clients honor it without bespoke logic.

### Consequences
- Positive: Cross-process correctness, idiomatic 429 response.
- Negative: One DB round-trip per request — accepted for round-2 scope; could later move to Redis if p95 budget tightens.

### Alternatives Considered
- **In-memory `asyncio.Lock` per worker**: rejected — every worker has its own bucket; user gets N×burst.
- **Sliding window in Redis**: rejected — out of scope; no Redis dep in the stack yet.
- **Token bucket via leaky-bucket cron**: rejected — adds a scheduler; the `FOR UPDATE` pattern is simpler.

## ADR-006: RFC 7807 `application/problem+json` Error Envelope

### Status
Accepted

### Context
FR-10 mandates a uniform error contract. Internal exceptions (validation, scope, rate-limit, subprocess timeout, DB error) must all serialize to the same shape and never leak stack trace, SQL fragment, or filesystem path (T-11 mitigation).

### Decision
A single `taskq_api.errors` module exposes `problem(status, type, detail, instance)` and registers exception handlers. All HTTP errors return `application/problem+json` with stable `type` URIs and a redacted `detail`. The DB URL with password, bearer tokens, and API keys are stripped by `redact()` before any log/metric emission (NFR-04).

### Rationale
- One module = one place to enforce redaction; auditing leaks is a single-file review.
- RFC 7807 is the standard HTTP problem envelope; clients parse it without bespoke logic.
- Static grep gate (`shell=True|eval(|exec(`) plus exception-handler coverage enforces T-11.

### Consequences
- Positive: Predictable clients, audit-friendly error surface, no accidental secret leak.
- Negative: Custom error subclasses must extend the `problem()` builder; no ad-hoc JSON dumps.

### Alternatives Considered
- **FastAPI's default `HTTPException` JSON**: rejected — body shape varies by status; no `type` URI.
- **Stack-trace in DEBUG only**: rejected — leaks when DEBUG is toggled on in prod by accident.
- **Per-endpoint custom error classes**: rejected — duplicates redaction logic.

## ADR-007: API Key Authentication with SHA-256 + hmac.compare_digest

### Status
Accepted

### Context
FR-03/04 require API-key auth with hierarchical scopes (`read < write < admin`). Constant-time comparison is required to defeat timing attacks (T-03); the stored value must not be reversible so a DB leak does not yield usable credentials.

### Decision
- Store only `SHA-256(key)` in `api_keys.key_hash` (never plaintext).
- Compare with `hmac.compare_digest(stored_hash, sha256(supplied_key))`.
- `api/deps.scope_dep` enforces hierarchical scope **before** any resource lookup to prevent existence-leak via 403 body (T-05 mitigation).

### Rationale
- SHA-256 + `hmac.compare_digest` is the Python-stdlib pattern for constant-time credential checks.
- Scope-before-lookup is the canonical "no info leak" idiom; the alternative (lookup then check) reveals existence via differential 403 vs 404.

### Consequences
- Positive: Constant-time compare, non-reversible storage, no existence leak.
- Negative: Key rotation requires a new row + revoke; old key rows linger (accepted — provides audit trail).

### Alternatives Considered
- **bcrypt / argon2 for the stored hash**: rejected — overkill for high-entropy random keys (32+ bytes); would slow legitimate auth without raising attacker cost meaningfully.
- **HMAC of key with server-side pepper**: rejected — requires a secret-loading story that is out of scope for round 2.
- **JWT with HS256**: rejected — adds token issuance + revocation state that round 2 does not need.

## ADR-008: Alembic v3 Data Migration with Reversible Downgrade

### Status
Accepted

### Context
FR-07 demands three real Alembic revisions. v3 splits `tasks.result_json` (a JSON blob) into a normalized `task_results` child table. A real downgrade must re-pack rows into `result_json` and drop the child table. A `downgrade()` that just `pass`es would fail NFR-09 traceability.

### Decision
- `v1_initial.py` creates `tasks`, `api_keys`, `rate_buckets`.
- `v2_tags.py` adds `tags`, `task_tags`, and a unique index on `tasks.name`.
- `v3_split_results.py` performs the column-to-table split with a data migration in `upgrade()` and re-pack in `downgrade()`.
- `make verify-system` exercises upgrade → downgrade -1 → upgrade → downgrade base → upgrade to prove round-trip integrity.

### Rationale
- Reversibility is the architectural invariant; downstream tooling assumes any revision can be undone.
- Real-world migrations must handle existing data; a no-op downgrade teaches nothing and blocks the round-trip check.

### Consequences
- Positive: Schema evolution is testable, rollback-able, and reviewed.
- Negative: v3 is denser than a no-op migration; the down-pack must be tested row-by-row.

### Alternatives Considered
- **Single big-bang initial migration**: rejected — NFR-07 traceability demands three revisions.
- **Expand-and-contract (v3a expand, v3b contract)**: rejected — splits one logical change across two revisions and obscures intent.
- **Skip real data, just `pass`**: rejected — fails NFR-09 traceability plus the Gate-2 round-trip check.

## ADR-009: `session_scope()` Context Manager for Transaction Boundaries

### Status
Accepted

### Context
FR-06 / NFR-03 require explicit transaction boundaries. `asyncio.CancelledError` must propagate untouched (no 500 conversion). Bare `except: pass` is forbidden (static lint).

### Decision
A single `taskq_api.repository.session.session_scope()` async context manager yields a `Session`, commits on success, rolls back on exception. Every repository function and every service-layer handler that mutates state must call it. `CancelledError` is re-raised without being wrapped.

### Rationale
- One boundary enforcement point = one place to audit for transaction correctness.
- Explicit commit/rollback removes ambiguity about partial writes (T-09 mitigation).
- Honoring `CancelledError` is required by NFR-03; converting it to 500 hides shutdown bugs.

### Consequences
- Positive: Auditable, uniform, mutation-safe.
- Negative: Every mutation site must remember to enter the context manager — enforced by code review and integration tests.

### Alternatives Considered
- **SQLAlchemy `session.begin()` per call**: rejected — implicit; no rollback guarantee on exception.
- **Manual `try/except/finally` everywhere**: rejected — duplicates the same code N times; one missed `rollback` corrupts state.
- **Unit-of-work pattern with shared session**: rejected — async session lifecycle gets messy; harder to reason about isolation.

## ADR-010: Module Hub Pattern (`__init__.py` Re-Exports)

### Status
Accepted

### Context
SAD §2.5 requires per-directory CRG cohesion. Each layer (`models`, `repository`, `service`, `api`) must register internal edges so community detection sees it as a single community. Without re-exports, the layer collapses into disconnected files.

### Decision
Each directory has an `__init__.py` hub that re-exports the public surface (`models.Task`, `repository.session_scope`, `service.require_scope`, `api.problem_response`, etc.). Sibling files call into the hub from **multiple function bodies** (not just at module level) so per-function edge counts offset external edges (httpx / fastapi / sqlalchemy / alembic).

### Rationale
- Re-exports break circular imports (a Python mechanism) and align with the CRG analysis contract.
- Calling hub helpers from ≥2 functions per file ensures each file contributes internal edges proportional to its external surface.

### Consequences
- Positive: Stable public surface, CRG-friendly structure.
- Negative: Slight indirection — readers must know the hub exports the helper.

### Alternatives Considered
- **Direct imports between siblings, no hub**: rejected — produces isolated files in CRG analysis; cohesion budget fails.
- **Single fat `__init__.py` per layer**: rejected — mixes module setup with class definitions; harder to read.
- **No re-exports, only side effects**: rejected — breaks downstream consumers expecting `from taskq_api.repository import session_scope`.

## ADR-011: License Allowlist + `requirements.lock` + SBOM

### Status
Accepted

### Context
NFR-07 requires every transitive dependency to fall into an allowlist (MIT / BSD-2 / BSD-3 / Apache-2.0 / PSF). The full tree must be scanned and an SBOM regenerated per release.

### Decision
Pin direct deps in `requirements.txt` with `==`. Pin the full transitive tree in `requirements.lock`. Run `pip-licenses --with-system` in CI; fail the build on disallowed licenses. Generate `08-config/SBOM.json` per release tag.

### Rationale
- Allowlist is small enough to audit manually; permissive open-source licenses cover the stack.
- `requirements.lock` makes `pip install` reproducible across machines.

### Consequences
- Positive: Reproducible installs, provable license posture.
- Negative: Lock file churn on every dep bump — accepted as the cost of reproducibility.

### Alternatives Considered
- **Loose pins (`>=`)**: rejected — breaks reproducibility and license audit.
- **Commercial-license dependencies**: rejected — none required; any commercial license would fail the allowlist.
- **Vendor everything**: rejected — adds maintenance cost; pins in lock file achieve the same result.

## ADR-012: `make verify-system` as Phase 3 Gate 2 Target

### Status
Accepted

### Context
Phase 3 Gate 2 invokes a single make target — `verify-system`. If it exits non-zero, Gate 2 fails. The target name is fixed by the harness.

### Decision
The Makefile exposes `verify-system`, which composes: `alembic upgrade head` → full test suite → service start + `/healthz` + `/readyz` smoke → `alembic downgrade base` → `alembic upgrade head`, then prints `verify-system: PASS`.

### Rationale
- A single end-to-end round-trip is the smallest proof that schema, runtime, health checks, and tests all agree.
- Upgrade → downgrade → upgrade is the most aggressive schema reversibility test possible.

### Consequences
- Positive: One command proves the system is shippable.
- Negative: Slower CI; mitigated by limiting the matrix to what the target actually needs.

### Alternatives Considered
- **Shell script outside the Makefile**: rejected — harness calls `make verify-system`; CI integration is the point.
- **Split into `verify-schema`, `verify-tests`, `verify-runtime`**: rejected — harness contract is one target.
- **Docker compose target**: rejected — adds infra complexity for round 2.

## ADR-013: Cross-Cutting Quality NFRs Enforced as Tooling Gates, Not Structure

### Status
Accepted

### Context
Four requirements in the specification are properties of *every* module rather than consequences of any one structural choice, so the traceability matrix above has no honest owning decision to point at:
- NFR-05 AC-N05.1 — every public function/class docstring references `[FR-XX]` or `[NFR-XX]`, at 100% coverage.
- NFR-08 — a mutation score ≥ 70, with the run scoped to `service/` and `repository/`.
- NFR-09 — no `pytest.skip` / `skipif` / `xfail` / assertion-free stub anywhere, and no removal of tests from collection.
- NFR-11 AC-N11.1–.3 — MI ≥ 80, per-function CC ≤ 10, ≤ 400 lines per file, ≤ 15 files per directory.

Attaching any of these to ADR-001..ADR-012 would misattribute a whole-codebase property to a single decision.

### Decision
Record them here as codebase-wide conventions whose enforcement is a tooling configuration, not an architectural structure:
- **NFR-05 (docstring half)**: the `[FR-XX]` / `[NFR-XX]` docstring reference is a writing convention applied to every public symbol. The OpenAPI half (AC-N05.2) is served structurally by ADR-002 and is not restated here.
- **NFR-08**: `.methodology/harness_config.json` sets `features.mutation_testing: true` and records the `service/` + `repository/` scope with its runtime-budget rationale; `mutmut run` → `mutmut results` must report ≥ 70.
- **NFR-09**: `pytest 03-development/tests -q` must report `skipped = 0`, every test function must contain at least one `assert`, and tests may not be dropped via `--ignore` / `-k` / `--deselect` / `collect_ignore` / `testpaths`.
- **NFR-11 (metric half)**: the MI / CC / file-size / directory-size budgets apply uniformly. AC-N11.4 (business logic in `service/`) is served structurally by ADR-001 and is not restated here.

### Rationale
- A dependency rule or a module layout cannot encode "every function has a docstring" or "no test is skipped"; the only decision available is *where the enforcement lives*, and the honest answer is the tool configuration.
- Stating this explicitly keeps the traceability matrix complete: every NFR in the SRS has a named owner, and none is silently credited to a decision that does not actually constrain it.

### Consequences
- Positive: No orphaned NFR; for each one the enforcement mechanism is written down rather than assumed.
- Negative: These four are exactly as strong as their configuration — narrowing the mutation scope or relaxing a threshold weakens them without changing a single architectural decision, so config changes to them warrant the same review weight as an ADR amendment.

### Alternatives Considered
- **Attach each to the nearest structural ADR**: rejected — misattributes a whole-codebase property to one decision and hides that the real enforcement is a tool setting.
- **Omit them from the ADR set**: rejected — the traceability matrix would then carry gaps for NFR-05, NFR-08, NFR-09 and NFR-11, which is the coverage failure this record exists to prevent.
- **One ADR per NFR**: rejected — four near-identical records ("set the flag, set the threshold") with no distinguishing decision content.