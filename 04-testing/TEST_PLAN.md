# Test Plan — taskq-api (P4)

> **Version**: v1.0
> **Date**: 2026-08-11
> **Phase**: 4 — Testing (CHECKPOINT-0 pre-per-FR authoring)
> **Source SRS**: `01-requirements/SRS.md` (canonical: `SPEC.md` v1.0.0)
> **Source manifest**: `.methodology/quality_manifest.json`
> **Scope**: All 10 FRs (FR-01..FR-10) + all 12 NFRs (NFR-01..NFR-12)
> **Test target**: `03-development/src/` (FastAPI ASGI service)
> **Verification target**: `make verify-system` (NFR-12)

---

## 0. Test Plan Conventions

| Field | Convention |
|-------|-----------|
| Test ID | `TP-{FR|NFR}-{AC-idx}-{category}` where `category ∈ {P,N,B,E}` = positive / negative / boundary / edge |
| Priority | P0 (must pass) / P1 (should pass) / P2 (nice to have) |
| Layer | unit / integration / e2e / static (grep/bandit/lint) / mutation |
| Module(s) | the implementation module(s) under test |
| Acceptance source | `SRS.md` §3 / §4 AC line (verbatim citation) |
| Verification method | the test mechanism (HTTP, SQLAlchemy event, grep, bandit, etc.) |
| Fixture / input | the input data or pre-condition to set up |
| Expected output | observable assertion (status code, body, log, file, exit code) |

**Categories:**
- **Positive (P)** — happy path; system behaves as specified under nominal input.
- **Negative (N)** — invalid input / unauthorized / forbidden / not-found; error contract compliance.
- **Boundary (B)** — values at or near limits (limit=0, limit=200, limit=201, name length=1000/1001, empty list).
- **Edge (E)** — unusual but valid states (concurrent bursts, drain at exact timeout, revoked key mid-session, offline migration back-online).

**Authority chain** (per HR-05 / `R-CANONICAL-INTERP-001`): each AC line below cites the source SPEC §-row; that source is the contract. This plan only operationalises the contract — it does not interpret or relax it.

---

## 1. FR-01 — Task Resource CRUD API

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-01 (canonical: `SPEC.md` §3 FR-01) |
| Module(s) | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.orm`, `taskq_api.models.schemas` |
| Verification path | `httpx.AsyncClient(transport=ASGITransport(app))`; `SQLAlchemy` event-listener for SQL count |
| AC map | AC-1.1 .. AC-1.7 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-01-1.1-P | P | P0 | Create task with valid `write` key, valid body, unique name | `POST /v1/tasks` `{name, command, scope, tags?}` + `X-API-Key` (write) | 201; body contains `id` (UUID); `Content-Type: application/json`; row in `tasks` table |
| TP-FR-01-1.1-B | B | P0 | Name exactly 1000 chars, command exactly 1000 chars | `POST /v1/tasks` with 1000-char `name` and 1000-char `command` | 201; persisted as-is (boundary) |
| TP-FR-01-1.1-E | E | P1 | Unicode name + emoji + non-ASCII command | `POST /v1/tasks` with UTF-8 multi-byte name and command | 201; persisted round-trips unchanged |
| TP-FR-01-1.2-N | N | P0 | Body missing required `name` | `POST /v1/tasks` `{}` | 422; `application/problem+json`; `detail` names missing field |
| TP-FR-01-1.2-N-2 | N | P0 | Body with empty `name` | `POST /v1/tasks` `{name: "", command: "..."}` | 422; `application/problem+json` |
| TP-FR-01-1.3-N | N | P0 | Get unknown id | `GET /v1/tasks/{random-uuid}` (read key) | 404; `application/problem+json`; `type`/`title`/`status`/`detail`/`instance`/`correlation_id` |
| TP-FR-01-1.4-N | N | P0 | Duplicate name collision | Create `T1` then `POST /v1/tasks` with same `name` | 409; `application/problem+json`; no second row in `tasks` |
| TP-FR-01-1.5-P | P | P0 | List endpoint uses cursor, not offset | `GET /v1/tasks?limit=10` → response body has `next_cursor` (opaque, NOT numeric offset) | 200; body contains `next_cursor` as opaque string; no `offset`/`page` fields |
| TP-FR-01-1.5-P-2 | P | P0 | Cursor round-trip across pages | `GET ?limit=10` → `next_cursor` → `GET ?limit=10&cursor=<that>` | 200; second page items do not overlap first; full sequence equals full list |
| TP-FR-01-1.6-B | B | P0 | `limit=0` | `GET /v1/tasks?limit=0` | 422; `application/problem+json` |
| TP-FR-01-1.6-B-2 | B | P0 | `limit=200` (max) | `GET /v1/tasks?limit=200` | 200; up to 200 items returned |
| TP-FR-01-1.6-N | N | P0 | `limit=201` | `GET /v1/tasks?limit=201` | 422; `application/problem+json` |
| TP-FR-01-1.7-P | P | P0 | N+1 guard — list SQL count is constant | Seed 1, 10, 100 tasks; `GET /v1/tasks?limit=200`; count SQL statements via SQLAlchemy `before_cursor_execute` event listener | Statement count identical across all 3 cases (e.g. 2–3 statements: 1 main + 0..1 eager loads) |
| TP-FR-01-1.7-E | E | P1 | N+1 guard under 10,000 rows (perf seed) | Seed 10,000 tasks; `GET /v1/tasks?limit=50` | Statement count unchanged from small dataset; p95 < 80 ms (NFR-01) |
| TP-FR-01-1.7-N | N | P0 | List filter `?status=failed` does not N+1 | Seed 10,000 mixed-status tasks; `GET /v1/tasks?status=failed&limit=200` | Constant statement count regardless of result-set size |
| TP-FR-01-DEL-P | P | P0 | Delete task (admin) | `DELETE /v1/tasks/{id}` with admin key | 204 (or 200); row + dependent `task_results` rows gone; FK cascade verified |
| TP-FR-01-DEL-N | N | P0 | Delete with read key (insufficient) | `DELETE /v1/tasks/{id}` with read key | 403; body does NOT reveal whether id exists |
| TP-FR-01-DEL-N-2 | N | P0 | Delete with write key (non-admin) | `DELETE /v1/tasks/{id}` with write key | 403; body does NOT reveal whether id exists |
| TP-FR-01-DEL-N-3 | N | P0 | Delete with no key | `DELETE /v1/tasks/{id}` no `X-API-Key` | 401; `application/problem+json` |

---

## 2. FR-02 — Task Execution Endpoint

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-02 (canonical: `SPEC.md` §3 FR-02) |
| Module(s) | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo` |
| Verification path | subprocess fixture (`/bin/echo`, `/bin/sleep`, `/bin/sh`); psutil for orphan check |
| AC map | AC-2.1 .. AC-2.5 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-02-2.1-P | P | P0 | Run valid task | `POST /v1/tasks/{id}/run` (write key) on a known task | 202; body contains `run_id` (UUID) |
| TP-FR-02-2.1-N | N | P0 | Run unknown task | `POST /v1/tasks/{random-uuid}/run` | 404; `application/problem+json` |
| TP-FR-02-2.1-N-2 | N | P0 | Run with read key (insufficient scope) | `POST /v1/tasks/{id}/run` with read key | 403; no `run_id` in body |
| TP-FR-02-2.2-P | P | P0 | Source uses `asyncio.create_subprocess_exec`, NOT `shell=True` | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | 0 hits (also covers NFR-02 AC-N02.1) |
| TP-FR-02-2.2-P-2 | P | P0 | Subprocess invocation does NOT use shell | Run `command="hello; rm -rf /"`; verify file system unchanged after run | No shell expansion; the `;` is treated as literal arg → command not found, no destructive side effect |
| TP-FR-02-2.2-E | E | P1 | Command with shell metacharacters runs literally | `command='echo $HOME'` (would expand under shell) | Stdout captured is literal `$HOME`, not the user's home dir |
| TP-FR-02-2.3-P | P | P0 | Timed-out task kills child, no orphan | `command="sleep 60"` with `TASKQ_TASK_TIMEOUT=1` | After timeout: child PID no longer alive (`ps -p <pid>` → not found); `task_results.exit_code` is non-zero (signal-killed); `task_results.finished_at` set |
| TP-FR-02-2.3-E | E | P0 | Timed-out process leaves zero descendants | Run `bash -c "sleep 60 &"` style command with timeout | Process tree below runner is empty; `pstree -p <runner-pid>` shows no child PIDs |
| TP-FR-02-2.4-P | P | P0 | Successful run records full result row | `command="echo hello"` → run → poll | `task_results` row has non-null `exit_code=0`, `stdout_tail="hello\n"`, `stderr_tail=""`, `duration_ms>0`, `finished_at` timestamp |
| TP-FR-02-2.4-N | N | P0 | Failed run records non-zero exit | `command="false"` | `task_results.exit_code != 0`; status transition → `failed` |
| TP-FR-02-2.4-B | B | P1 | Large stdout truncated to tail | `command="yes A | head -c 200000"` | `stdout_tail` is bounded (≤ TASKQ_STDOUT_TAIL_BYTES); never blows up row size |
| TP-FR-02-2.5-P | P | P0 | Run history is newest-first | Run task 3 times in sequence; `GET /v1/tasks/{id}/runs` | First item in response corresponds to the third run; ordering strictly descending by `finished_at` (or `started_at`) |
| TP-FR-02-2.5-N | N | P0 | Run history for unknown id | `GET /v1/tasks/{unknown}/runs` | 404; `application/problem+json` |
| TP-FR-02-2.5-B | B | P0 | Run history paginated | Run 5 times; `GET /v1/tasks/{id}/runs?limit=2` | 200; returns 2; `next_cursor` present; full sequence recoverable via cursor walk |

---

## 3. FR-03 — API Key Authentication

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-03 (canonical: `SPEC.md` §3 FR-03) |
| Module(s) | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo` |
| Verification path | `httpx` with / without `X-API-Key`; direct DB query of `api_keys`; CLI subprocess `python -m taskq_api key create` |
| AC map | AC-3.1 .. AC-3.5 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-03-3.1-N | N | P0 | No `X-API-Key` header | `POST /v1/tasks` no header | 401; `application/problem+json` |
| TP-FR-03-3.1-N-2 | N | P0 | Empty `X-API-Key` header | `X-API-Key: ""` | 401; same contract as above |
| TP-FR-03-3.1-N-3 | N | P0 | Malformed `X-API-Key` (non-base64 garbage) | `X-API-Key: "not-a-key"` | 401; same contract |
| TP-FR-03-3.2-P | P | P0 | `api_keys` rows store 64-hex SHA-256, no plaintext | `python -m taskq_api key create --scope write` → inspect DB row | `key_hash` matches `/^[0-9a-f]{64}$/`; no plaintext column; no plaintext log line |
| TP-FR-03-3.2-N | N | P0 | Plaintext never appears in DB | After key create, dump `api_keys` table | Plaintext token string NOT present in any column (including comments / error tables) |
| TP-FR-03-3.3-P | P | P0 | Comparison uses `hmac.compare_digest` | `grep -n "compare_digest" 03-development/src/` | ≥1 hit in auth path |
| TP-FR-03-3.3-E | E | P1 | Constant-time: brute-force timing not exploitable | Send 100 valid + 100 invalid keys; measure wall-clock | Mean time diff < 5× (sanity; not a strict T-leak test) |
| TP-FR-03-3.4-N | N | P0 | Revoked key rejected | `UPDATE api_keys SET revoked_at=NOW() WHERE id=...`; retry with that key | 401; same contract; no information leak about revocation reason |
| TP-FR-03-3.4-B | B | P0 | Revoked key at exact revocation timestamp boundary | Revoke then immediately retry | 401; no race window where revoked key still authenticates |
| TP-FR-03-3.5-P | P | P0 | Plaintext printed exactly once | `python -m taskq_api key create --scope write` | stdout contains plaintext token exactly once; no other stdout / log line contains it |
| TP-FR-03-3.5-N | N | P0 | Plaintext not persisted to log | After key create, `grep -r <token> .` (excluding the intended stdout stream) | Token absent from `*.log`, `*.db`, `*.sqlite`, `*.json` artefacts |
| TP-FR-03-3.5-E | E | P1 | Plaintext not in `/v1/metrics` | After key create, `GET /v1/metrics` (admin) | Response body contains no plaintext token (covered by NFR-04) |

---

## 4. FR-04 — Scope Authorisation

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-04 (canonical: `SPEC.md` §3 FR-04) |
| Module(s) | `taskq_api.api.deps`, `taskq_api.service.auth` |
| Verification path | route-table inspection (`app.routes`); per-route dependency chain |
| AC map | AC-4.1 .. AC-4.3 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-04-4.1-N | N | P0 | Delete with write key | `DELETE /v1/tasks/{id}` write key | 403; `detail` does NOT include id; `detail` does NOT say "not found" / "exists" |
| TP-FR-04-4.1-N-2 | N | P0 | Delete unknown id with write key | `DELETE /v1/tasks/{unknown}` write key | 403; body byte-equal (or field-equal) to the known-id 403 — no existence oracle |
| TP-FR-04-4.1-N-3 | N | P0 | 403 body never contains "not found" / "unknown" / "exists" | Inspect 403 body for any substring of those | 0 substring matches |
| TP-FR-04-4.2-P | P | P0 | All `/v1` routes go through a single dependency | Iterate `app.routes`; collect `dependencies` for each `/v1` path | Every `/v1` route's `dependencies` set contains exactly one auth dep (`require_scope` or similar) |
| TP-FR-04-4.2-N | N | P0 | Authorisation is NOT duplicated in handlers | `grep -n "scope" 03-development/src/taskq_api/api/tasks.py` (or equivalent handler) | 0 hits (i.e. no per-handler scope check) — single point of decision in `api/deps.py` |
| TP-FR-04-4.3-P | P | P0 | Hierarchy: read ⊂ write ⊂ admin | Token with scope=`read` calls `POST /v1/tasks` (write-required) | 403 (read is insufficient for write) |
| TP-FR-04-4.3-P-2 | P | P0 | Hierarchy: write ⊂ admin | Token with scope=`write` calls `DELETE /v1/tasks/{id}` (admin-required) | 403 |
| TP-FR-04-4.3-P-3 | P | P0 | Admin key satisfies write requirement | Token with scope=`admin` calls `POST /v1/tasks` | 201 (or appropriate success) |
| TP-FR-04-4.3-P-4 | P | P0 | Admin key satisfies read requirement | Token with scope=`admin` calls `GET /v1/tasks` | 200 |
| TP-FR-04-4.3-B | B | P0 | No scope above `admin` | Attempt to use `scope=superadmin` | Rejected; admin is the top of the hierarchy |
| TP-FR-04-4.3-N | N | P0 | Unknown scope value | Token with `scope=garbage` | 401/403; treated as invalid scope (no implicit default) |

---

## 5. FR-05 — Rate Limiting

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-05 (canonical: `SPEC.md` §3 FR-05) |
| Module(s) | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo` |
| Verification path | burst loop with `httpx`; DB row inspection of `rate_buckets` |
| AC map | AC-5.1 .. AC-5.3 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-05-5.1-N | N | P0 | Burst exceeds `TASKQ_RATE_BURST` | Configure `BURST=3`; fire 10 requests in <1s | 1st..3rd: success; 4th..10th: 429; `Retry-After` header present (numeric seconds) |
| TP-FR-05-5.1-B | B | P0 | Exactly at burst boundary | Fire exactly `BURST` requests then 1 more | First `BURST` succeed; the next 1 is 429 |
| TP-FR-05-5.1-P | P | P0 | Retry-After is numeric and ≥ 1 | 429 response | `Retry-After` parses as int ≥ 1 |
| TP-FR-05-5.1-E | E | P1 | Burst + wait → recovery | Burst → 429 → wait `TASKQ_RATE_PER_SEC` seconds | 1 token refilled; next request succeeds |
| TP-FR-05-5.2-P | P | P0 | Bucket state is in DB | After burst, inspect `rate_buckets` table | One row per `api_keys.id`; `tokens` and `last_refill_at` columns updated |
| TP-FR-05-5.2-P-2 | P | P0 | Bucket update is single-transactional | Concurrent burst from 2 processes against same key | Total admitted ≤ `BURST`; no over-admit race (no double-spend of tokens) |
| TP-FR-05-5.2-P-3 | P | P0 | Row-level lock in use | `grep -rn "with_for_update" 03-development/src/` | ≥1 hit in `repository/rate_repo` |
| TP-FR-05-5.3-P | P | P0 | `/healthz` not rate-limited | Fire 1000 GETs to `/healthz` with no key | All 200; no 429; no DB row in `rate_buckets` created for healthz traffic |
| TP-FR-05-5.3-P-2 | P | P0 | `/readyz` not rate-limited | Fire 1000 GETs to `/readyz` with no key | All 200 (or 503 if DB down, but never 429) |
| TP-FR-05-5.3-B | B | P0 | Rate-limited endpoint `/v1/tasks` IS rate-limited | Fire `BURST+1` to `/v1/tasks` | 429 on overflow |
| TP-FR-05-5.3-E | E | P1 | Per-token isolation — token A's burst does not affect token B | Burst token A, then send with token B | Token B requests all succeed |

---

## 6. FR-06 — Persistence Layer and Transaction Boundaries

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-06 (canonical: `SPEC.md` §3 FR-06) |
| Module(s) | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` |
| Verification path | static (`grep`, AST) + dynamic (force exception mid-transaction, verify rollback) |
| AC map | AC-6.1 .. AC-6.5 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-06-6.1-P | P | P0 | `sqlalchemy` import is only in `repository/` | `grep -rn "^import sqlalchemy\|^from sqlalchemy" 03-development/src/` | Hits only in `repository/`; zero hits in `api/`, `service/`, `models/`, `errors/`, `config/`, `__main__` (also covered by `lint-imports`) |
| TP-FR-06-6.1-N | N | P0 | No `Session` held in service layer | `grep -rn "Session" 03-development/src/taskq_api/service/` | 0 hits on the SQLAlchemy `Session` type |
| TP-FR-06-6.2-P | P | P0 | Context manager enforces commit on success | Patch repository function to raise; run request | Exception propagates; no partial row created (rollback verified) |
| TP-FR-06-6.2-N | N | P0 | Context manager enforces rollback on exception | Force `IntegrityError` mid-request | 409/422 returned; no orphan row in `tasks` |
| TP-FR-06-6.2-E | E | P1 | Exception in nested repository call still rolls back outer txn | Monkey-patch nested repo to throw | Outer transaction rolled back; no partial multi-table write |
| TP-FR-06-6.3-P | P | P0 | No string-concatenated SQL | `grep -rEn 'f"[^"]*SELECT\|f"[^"]*INSERT\|f"[^"]*UPDATE\|f"[^"]*DELETE\|"[^"]*"\s*%\s*\(|"[^"]*"\s*\+\s*"' 03-development/src/` (and similar patterns) | 0 hits in any non-test source file (also covered by NFR-02) |
| TP-FR-06-6.4-P | P | P0 | List endpoint SQL count is constant (N+1) | Same as FR-01 TP-FR-01-1.7-P | Statement count constant regardless of row count |
| TP-FR-06-6.4-E | E | P1 | Detail GET (single row) statement count is bounded | `GET /v1/tasks/{id}`; count statements | ≤ constant bound (≤3) — no N+1 even with eager-loaded tags/results |
| TP-FR-06-6.5-P | P | P0 | `pool_size=TASKQ_DB_POOL_SIZE` configured | Inspect engine config in `repository/session.py` | `pool_size=int(os.environ["TASKQ_DB_POOL_SIZE"])` set on engine |
| TP-FR-06-6.5-P-2 | P | P0 | `pool_pre_ping=True` configured | Inspect engine config | `pool_pre_ping=True` present on engine |
| TP-FR-06-6.5-E | E | P1 | `pool_pre_ping` recovers from stale connection | Kill a backend connection externally; next request | Request succeeds; pre-ping reconnects |

---

## 7. FR-07 — Schema Migration

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-07 (canonical: `SPEC.md` §3 FR-07) |
| Module(s) | `migrations.versions.v1_initial`, `migrations.versions.v2_tags`, `migrations.versions.v3_split_results`, `taskq_api.repository.session` |
| Verification path | real SQLite DB file (NOT in-memory mock) per C-18 / NFR-09 |
| AC map | AC-7.1 .. AC-7.5 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-07-7.1-P | P | P0 | `alembic upgrade head` on fresh empty DB | `tmpfile = make_sqlite_tmpfile(); alembic upgrade head` | exit 0; `tasks`, `api_keys`, `tags`, `task_tags`, `task_results` tables exist; `alembic_version` row present |
| TP-FR-07-7.1-N | N | P0 | `upgrade head` against non-empty conflicting DB | Pre-create a conflicting `tasks` table | Fails with clear error (NOT silent drop) |
| TP-FR-07-7.2-P | P | P0 | `alembic downgrade base` | `upgrade head` → `downgrade base` | exit 0; zero application tables (`tasks`, `api_keys`, etc.) remain; only `alembic_version` (or empty DB) |
| TP-FR-07-7.3-P | P | P0 | Round-trip v3 preserves data byte-identical | `upgrade head` → seed `task_results` row with `{exit_code, stdout_tail, stderr_tail, duration_ms, finished_at}` → `downgrade -1` → `upgrade head` | Row(s) recoverable; each column byte-equal to pre-downgrade snapshot |
| TP-FR-07-7.3-B | B | P0 | Round-trip with multiple rows | Seed 10 rows; round-trip | All 10 rows preserved; all columns byte-equal |
| TP-FR-07-7.3-E | E | P0 | Round-trip with NULL and empty-string columns | Seed with `stdout_tail=""`, `stderr_tail=NULL`, `duration_ms=0`; round-trip | All edge values preserved |
| TP-FR-07-7.3-E-2 | E | P0 | Round-trip with multi-byte UTF-8 | Seed `stdout_tail="日本語 emoji 🎉"`; round-trip | Bytes unchanged |
| TP-FR-07-7.4-N | N | P0 | No `op.execute("DROP TABLE ...")` shortcut | `grep -rn 'op.execute.*DROP TABLE' 03-development/migrations/` | 0 hits |
| TP-FR-07-7.5-P | P | P0 | v1, v2, v3 each have working `downgrade` | `alembic upgrade head` → `downgrade -1` (to v2) → `downgrade -1` (to v1) → `downgrade base` | All transitions exit 0; data invariants hold at each step |
| TP-FR-07-7.5-P-2 | P | P0 | Migration is real-DB tested (NFR-09) | Test fixture uses `tmp_path / "roundtrip.db"` (a real file) | Test asserts the file path is a file on disk (not `:memory:`) |
| TP-FR-07-7.5-N | N | P0 | No `--ignore` / `-k` / `collect_ignore` for migration tests | `grep -rn "collect_ignore\|@pytest.mark.skip" 03-development/tests/test_fr07*` | 0 hits |
| TP-FR-07-7.5-P-3 | P | P0 | Offline SQL generation also tested | `alembic upgrade head --sql` | Produces SQL string; assertions on expected DDL fragments |

---

## 8. FR-08 — Asynchronous Executor

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-08 (canonical: `SPEC.md` §3 FR-08) |
| Module(s) | `taskq_api.service.runner` |
| Verification path | concurrency instrumentation; shutdown signal; psutil process tree |
| AC map | AC-8.1 .. AC-8.4 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-08-8.1-P | P | P0 | Concurrency cap respected | Configure `TASKQ_MAX_CONCURRENT=2`; fire 10 long-running tasks | At no observed instant are >2 `subprocess.Popen`-equivalent children running |
| TP-FR-08-8.1-B | B | P0 | Over-cap requests are queued | Same as above with throughput timer | All 10 eventually complete; total elapsed ≥ `(10/2) × per-task-time`; none lost |
| TP-FR-08-8.1-N | N | P0 | Cap not silently exceeded | During burst, sample running PIDs every 100ms | Max simultaneous children ≤ `TASKQ_MAX_CONCURRENT` |
| TP-FR-08-8.2-P | P | P0 | Graceful drain on shutdown | Start service; submit 3 long-running tasks; send `SIGTERM`/shutdown | In-flight tasks complete; `task_results.finished_at` set; service exits within `TASKQ_DRAIN_TIMEOUT` |
| TP-FR-08-8.2-B | B | P0 | Drain timeout boundary | Tasks with `runtime > TASKQ_DRAIN_TIMEOUT` | Marked `interrupted`; not forcibly killed mid-write |
| TP-FR-08-8.2-E | E | P0 | Over-budget tasks marked `interrupted`, not silently dropped | After drain timeout | DB row exists with status `interrupted`; no orphan process |
| TP-FR-08-8.3-P | P | P0 | Timeout kills child, no orphan | `command="sleep 60"` with timeout=1s | After timeout: child gone; parent runner still healthy; ready for next task |
| TP-FR-08-8.3-P-2 | P | P0 | `process.kill()` + `await process.wait()` sequence | `grep -A2 "wait_for\|TimeoutError" 03-development/src/taskq_api/service/runner.py` | Code path shows `process.kill()` followed by `await process.wait()` |
| TP-FR-08-8.3-E | E | P0 | Children-of-children also terminated | Command `bash -c "sleep 60 &"` style | Entire process tree below runner is empty after timeout |
| TP-FR-08-8.4-P | P | P0 | `CancelledError` propagates, not swallowed | `grep -rn "except Exception" 03-development/src/taskq_api/service/runner.py` | No `except Exception` clause that doesn't re-raise `CancelledError`; no `except BaseException:` |
| TP-FR-08-8.4-P-2 | P | P0 | Dynamic: cancel runner mid-task | Cancel the awaiting task; assert `CancelledError` is raised (not `Exception`) | Test sees `asyncio.CancelledError` propagating up |
| TP-FR-08-8.4-N | N | P0 | No `except Exception: pass` | `grep -rn "except Exception: pass\|except: pass" 03-development/src/` | 0 hits |

---

## 9. FR-09 — Health Checks and Observability

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-09 (canonical: `SPEC.md` §3 FR-09) |
| Module(s) | `taskq_api.api.health`, `taskq_api.repository.session`, `taskq_api.__main__` |
| Verification path | live HTTP; DB unavailability simulation; alembic current inspection |
| AC map | AC-9.1 .. AC-9.5 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-09-9.1-N | N | P0 | DB unreachable → 503 | Stop DB (point to closed sqlite file) → `GET /readyz` | 503; `detail` mentions DB-unavailable; `application/problem+json` |
| TP-FR-09-9.2-N | N | P0 | `alembic current` ≠ head → 503 | `downgrade -1` (so head is v2) → `GET /readyz` | 503; `detail` mentions migration-not-at-head |
| TP-FR-09-9.3-P | P | P0 | Deploy without migration fails closed | Fresh DB at v1 only; run service; `GET /readyz` | 503; service is NOT marked ready |
| TP-FR-09-9.4-P | P | P0 | `/healthz` no auth, no rate limit | `GET /healthz` no headers | 200; `{"status":"ok"}`; no 401; no 429 even under burst |
| TP-FR-09-9.4-P-2 | P | P0 | `/readyz` no auth, no rate limit | `GET /readyz` no headers | 200/503 depending on state; never 401; never 429 |
| TP-FR-09-9.5-P | P | P0 | `/v1/metrics` requires admin | `GET /v1/metrics` with read key | 403 |
| TP-FR-09-9.5-P-2 | P | P0 | `/v1/metrics` with admin returns counts | `GET /v1/metrics` with admin key after seeding tasks in various states | 200; body has task counts by status, execution latency percentiles, rate-limit rejection counts |
| TP-FR-09-9.5-E | E | P1 | `/v1/metrics` redacts DB password (NFR-04) | Set `TASKQ_DB_URL=postgresql://user:secret@host/db`; trigger any code path that might leak URL | No `secret` substring in any `/v1/metrics` response |
| TP-FR-09-9.5-B | B | P0 | `/v1/metrics` body is well-typed | Validate JSON schema | `task_counts: dict[str, int]`, `latency_p50/p95/p99: number`, `rate_limit_rejections: int` |

---

## 10. FR-10 — Error Contract (RFC 7807)

| Field | Value |
|-------|-------|
| Source | `SRS.md` §3 FR-10 (canonical: `SPEC.md` §3 FR-10) |
| Module(s) | `taskq_api.errors`, `taskq_api.api.deps` |
| Verification path | every non-2xx path; response header / body inspection; log scrape |
| AC map | AC-10.1 .. AC-10.4 |

| Test ID | Cat. | Pri. | Description | Input | Expected Output |
|---------|------|------|-------------|-------|-----------------|
| TP-FR-10-10.1-P | P | P0 | Every non-2xx has `application/problem+json` | Cover 401, 403, 404, 409, 422, 429, 500, 503 paths | All return `Content-Type: application/problem+json` |
| TP-FR-10-10.1-P-2 | P | P0 | Body has all 6 RFC 7807 fields | Inspect a sample 4xx body | Keys present: `type`, `title`, `status`, `detail`, `instance`, `correlation_id` |
| TP-FR-10-10.2-N | N | P0 | Trigger 500; body has no stack/SQL/path | Monkey-patch a handler to raise; inspect 500 body | No `Traceback`, no `SELECT`/`UPDATE`/`INSERT`/`DELETE`, no `/Users/...` or `\\` path substrings |
| TP-FR-10-10.2-N-2 | N | P0 | Trigger 500; log line may contain details, but body may not | Same trigger | Body bytes == log-redacted contract; only log captures stack |
| TP-FR-10-10.3-P | P | P0 | `correlation_id` in body == `X-Correlation-Id` response header | Trigger any 4xx | Header value == `body.correlation_id` |
| TP-FR-10-10.3-P-2 | P | P0 | `correlation_id` appears in log entry | Trigger 4xx; scrape `*.log` | Log line contains the same `correlation_id` |
| TP-FR-10-10.3-B | B | P0 | Client-supplied `X-Correlation-Id` is honored if present | `X-Correlation-Id: my-id`; trigger 4xx | Response body & log both use `my-id` |
| TP-FR-10-10.3-E | E | P1 | Missing client correlation → server-generated UUID | No header; trigger 4xx | Body has UUID-formatted `correlation_id` |
| TP-FR-10-10.4-P | P | P0 | Status code mapping matches §7 | One example each: 401, 403, 404, 409, 422, 429, 500, 503 | All match the table |

---

## 11. NFR Coverage (mapped to FRs above + standalone checks)

| Test ID | Cat. | Pri. | NFR | Description | Method | Expected Output |
|---------|------|------|-----|-------------|--------|-----------------|
| TP-NFR-01-A | P | P0 | NFR-01 | `GET /v1/tasks/{id}` p95 < 30 ms at 10k rows | pytest-benchmark with ASGITransport | p95 < 30 ms; recorded in benchmark output |
| TP-NFR-01-B | P | P0 | NFR-01 | `GET /v1/tasks?limit=50` p95 < 80 ms at 10k rows | pytest-benchmark | p95 < 80 ms |
| TP-NFR-01-C | P | P0 | NFR-01 | SQL statement count constant (N+1) | SQLAlchemy event listener | Constant count for 1, 100, 10k rows |
| TP-NFR-02-A | P | P0 | NFR-02 | `grep -rn "shell=True\|eval(\|exec(" 03-development/src/` | shell | 0 hits |
| TP-NFR-02-B | P | P0 | NFR-02 | No SQL string concatenation in `src/` | grep | 0 hits |
| TP-NFR-02-C | P | P0 | NFR-02 | Hashed keys + `hmac.compare_digest` (FR-03) | unit + grep | 0 plaintext, ≥1 `compare_digest` call |
| TP-NFR-02-D | P | P0 | NFR-02 | 403 hides existence (FR-04) | integration | 403 bodies field-equal for known + unknown id |
| TP-NFR-02-E | P | P0 | NFR-02 | 500 body has no stack/SQL/path (FR-10) | integration | 0 substring matches |
| TP-NFR-02-F | P | P0 | NFR-02 | CORS denies all by default | `OPTIONS /v1/tasks` with `Origin: https://attacker.tld` | `Access-Control-Allow-Origin` not set (or origin not echoed) |
| TP-NFR-02-G | P | P0 | NFR-02 | `bandit -r 03-development/src/` | shell | 0 HIGH, 0 MEDIUM |
| TP-NFR-03-A | P | P0 | NFR-03 | Context manager commit/rollback (FR-06) | integration | Rollback on forced exception |
| TP-NFR-03-B | P | P0 | NFR-03 | No bare except / `except Exception: pass` | grep | 0 hits |
| TP-NFR-03-C | P | P0 | NFR-03 | `CancelledError` propagates (FR-08) | integration | `CancelledError` raised to test, not swallowed |
| TP-NFR-03-D | P | P0 | NFR-03 | DB-down surfaces as `/readyz` 503 (FR-09) | integration | 503 with explicit `detail` |
| TP-NFR-03-E | P | P0 | NFR-03 | Timeout kills child (FR-08) | integration | Child gone after timeout |
| TP-NFR-03-F | P | P0 | NFR-03 | Failed migration rolls back (FR-07) | integration | After failed upgrade, prior revision is current |
| TP-NFR-04-A | P | P0 | NFR-04 | Redaction regex matches `sk-...`, `token=...`, `Bearer ...`, `postgres://...` | unit | Each pattern replaced wholesale with `[REDACTED]` |
| TP-NFR-04-B | P | P0 | NFR-04 | DB password not in any log / error / `/v1/metrics` | log scrape + HTTP | 0 hits for password fragment |
| TP-NFR-04-C | P | P0 | NFR-04 | API key plaintext printed exactly once (FR-03) | CLI | stdout has plaintext; no other persistent trace |
| TP-NFR-05-A | P | P0 | NFR-05 | 100% public symbols have `[FR-XX]` / `[NFR-XX]` docstring | AST scan | Coverage = 100% |
| TP-NFR-05-B | P | P0 | NFR-05 | Every route has OpenAPI `summary` + `description` | `GET /openapi.json` + iteration | All paths have non-empty summary + description |
| TP-NFR-06-A | P | P0 | NFR-06 | `.importlinter` exists, layers contract present | shell | `cat .importlinter`; contract `api > service > repository > models` present |
| TP-NFR-06-B | P | P0 | NFR-06 | sqlalchemy forbidden contract | shell | `lint-imports` exits 0; contract blocks sqlalchemy outside `repository/` |
| TP-NFR-06-C | P | P0 | NFR-06 | `lint-imports` exit 0 | shell | exit 0; no waiver / wildcard ignore |
| TP-NFR-07-A | P | P0 | NFR-07 | All deps pinned with `==` in `requirements.txt`; `requirements.lock` full | file scan | 0 unpinned / `>=` in `requirements.txt`; `requirements.lock` complete |
| TP-NFR-07-B | P | P0 | NFR-07 | License allowlist (MIT/BSD-2/BSD-3/Apache-2.0/PSF) | `pip-licenses --format=json --with-system` | Every dep ∈ allowlist |
| TP-NFR-07-C | P | P0 | NFR-07 | SBOM at `08-config/SBOM.json` | file | File exists, has `name/version/license/direct-or-transitive` per dep |
| TP-NFR-08-A | P | P0 | NFR-08 | `features.mutation_testing: true` in `harness_config.json` | file | JSON field set |
| TP-NFR-08-B | P | P0 | NFR-08 | Mutation score ≥ 70 over `service/` + `repository/` | `mutmut run` + `results` | score ≥ 70 |
| TP-NFR-08-C | P | P0 | NFR-08 | Scope is `service/` + `repository/` | `harness_config.json` | Scope fields present with budget rationale |
| TP-NFR-09-A | P | P0 | NFR-09 | No `skip` / `skipif` / `xfail` / assertion-free stubs in `tests/` | grep | 0 hits |
| TP-NFR-09-B | P | P0 | NFR-09 | `pytest -q` shows `skipped = 0` | shell | `0 skipped` |
| TP-NFR-09-C | P | P0 | NFR-09 | Every test has ≥ 1 `assert` | AST | `zero_assert == 0` |
| TP-NFR-09-D | P | P0 | NFR-09 | No `--ignore` / `-k` / `collect_ignore` in CI/testpaths | shell + file | 0 hits |
| TP-NFR-09-E | P | P0 | NFR-09 | FR-07 round-trip uses real SQLite file | file | Test fixture creates a file on disk (not `:memory:`) |
| TP-NFR-09-F | P | P0 | NFR-09 | `TRACEABILITY_MATRIX.md` `VERIFIED` only set after test pass | file + harness | No premature `VERIFIED` flag |
| TP-NFR-10-A | P | P0 | NFR-10 | Integration line coverage ≥ 80% | `pytest --cov=03-development/src tests/integration` | ≥ 80% |
| TP-NFR-10-B | P | P0 | NFR-10 | Integration via `httpx.ASGITransport` (no direct handler calls) | grep | No `from taskq_api.api.tasks import create_task` direct call in integration tests |
| TP-NFR-10-C | P | P0 | NFR-10 | Coverage includes all error codes | integration | 401, 403, 404, 409, 422, 429, 503 each has ≥ 1 test |
| TP-NFR-11-A | P | P0 | NFR-11 | MI ≥ 80 | readability-v2 | Project MI ≥ 80 |
| TP-NFR-11-B | P | P0 | NFR-11 | Per-function CC ≤ 10 | readability-v2 / radon | 0 functions with CC > 10 |
| TP-NFR-11-C | P | P0 | NFR-11 | File ≤ 400 lines; directory ≤ 15 files | readability-v2 | 0 violations |
| TP-NFR-11-D | P | P0 | NFR-11 | API handler ≤ 40 lines | readability-v2 | 0 violations |
| TP-NFR-12-A | P | P0 | NFR-12 | `make verify-system` chains the required steps | file | Target present with full recipe |
| TP-NFR-12-B | P | P0 | NFR-12 | `make verify-system` exit 0 + prints `verify-system: PASS` | shell | exit 0; stdout matches `verify-system: PASS` |

---

## 12. Acceptance Run-List Cross-Map (SRS §5 / SPEC §8)

| Spec # | Command | Covered by |
|--------|---------|------------|
| 1 | `pytest 03-development/tests -q` → 0 skipped | TP-NFR-09-B |
| 2 | `pytest --cov=03-development/src` → 100% | TP-NFR-11 (overall) + TP-FR-01..10 |
| 3 | `pytest tests/integration --cov=03-development/src` → ≥80% | TP-NFR-10-A |
| 4 | `POST /v1/tasks` (write key) → 201 | TP-FR-01-1.1-P |
| 5 | `POST /v1/tasks` (no key) → 401 | TP-FR-03-3.1-N |
| 6 | `DELETE /v1/tasks/{id}` (write key) → 403, no existence leak | TP-FR-04-4.1-N, TP-FR-04-4.1-N-2 |
| 7 | `GET /v1/tasks/{unknown}` → 404 | TP-FR-01-1.3-N |
| 8 | duplicate name → 409 | TP-FR-01-1.4-N |
| 9 | burst → 429 + Retry-After | TP-FR-05-5.1-N |
| 10 | DB down → 503 | TP-FR-09-9.1-N |
| 11 | downgrade -1 → 503 | TP-FR-09-9.2-N |
| 12 | v3 round-trip byte-identical | TP-FR-07-7.3-P |
| 13 | `downgrade base` → 0 residual tables | TP-FR-07-7.2-P |
| 14 | list SQL count constant | TP-FR-01-1.7-P, TP-FR-06-6.4-P |
| 15 | `GET /v1/tasks/{id}` p95 < 30 ms | TP-NFR-01-A |
| 16 | grep shell=True/eval/exec → 0 | TP-NFR-02-A |
| 17 | SQL string concat scan → 0 | TP-NFR-02-B |
| 18 | api_keys table → 0 plaintext, 64-hex | TP-FR-03-3.2-P |
| 19 | 500 body has no stack/SQL/path | TP-FR-10-10.2-N |
| 20 | logs + /v1/metrics → no DB password | TP-NFR-04-B |
| 21 | `lint-imports` → 0 | TP-NFR-06-C |
| 22 | `pip-licenses --with-system` → all in allowlist | TP-NFR-07-B |
| 23 | `bandit -r src/` → 0 HIGH/MEDIUM | TP-NFR-02-G |
| 24 | `mutmut results` → ≥ 70 | TP-NFR-08-B |
| 25 | shutdown → graceful drain, no orphans | TP-FR-08-8.2-P, TP-FR-08-8.3-P |
| 26 | `grep -c "^TASKQ_" .env.example` → 12 | `.env.example` content audit |
| 27 | `make verify-system` → exit 0 + `verify-system: PASS` | TP-NFR-12-B |

---

## 13. Test Matrix Summary (per-FR coverage count)

| FR/NFR | Positive | Negative | Boundary | Edge | Total |
|--------|----------|----------|----------|------|-------|
| FR-01  | 4 | 7 | 5 | 2 | 18 |
| FR-02  | 4 | 3 | 1 | 4 | 12 |
| FR-03  | 3 | 5 | 1 | 3 | 12 |
| FR-04  | 4 | 3 | 1 | 1 | 9 |
| FR-05  | 4 | 1 | 3 | 2 | 10 |
| FR-06  | 6 | 3 | 0 | 2 | 11 |
| FR-07  | 6 | 2 | 1 | 2 | 11 |
| FR-08  | 4 | 2 | 1 | 3 | 10 |
| FR-09  | 4 | 2 | 1 | 1 | 8 |
| FR-10  | 4 | 2 | 1 | 1 | 8 |
| NFR-01..12 | — | — | — | — | 33 |
| **Total** | **41** | **30** | **15** | **21** | **142** |

---

## 14. Out-of-Plan (explicit deferrals)

- **TypeScript service** — round 3, out of scope (SRS §6).
- **CLI tool (`taskq-plus`)** — round 1, out of scope.
- **Distributed task scheduling** — out of scope per SRS §6.
- **Webhooks** — out of scope per SRS §6.

---

## 15. Self-Review

- **Possible errors:**
  1. Mapping SPEC §-row numbers to test cases can drift if the SPEC adds new rows; re-run cross-check at every Gate.
  2. P0/pytest-benchmark timing tests on macOS CI runners can be noisy; if p95 is at the boundary, increase sample size or warm-up, but never lower the threshold.
- **Unverified assumptions:**
  - NFR-08 mutation score target is per the manifest's `mutation_testing` threshold (70) and SPEC §4 NFR-08; not yet re-confirmed against a fresh `mutmut` run.
  - NFR-12 `verify-system` recipe is per the SPEC's chain description; the concrete `Makefile` was authored in Phase 2 but not re-read here.
- **Confidence:** High on FR mapping (one-to-one with SRS ACs); Medium on NFR-08/-12 score thresholds until the matching tools are re-run.
- **If this plan is wrong, the most likely error is:** forgetting a new AC added in a later SPEC round. Mitigation: re-derive AC list from `SRS.md` §3/§4 at every Phase 4 entry, not just once.
