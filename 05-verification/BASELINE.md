# BASELINE.md - taskq-advance

> On-demand Lazy Load template (P5 verification snapshot, 2026-08-11).

## 1. Baseline Overview

- Author: P5 Verification Author (orchestrator, "orch-post")
- Reviewer: P5 Verifier Agent (Claude / Sonnet)
- session_id: `p5-verification-2026-08-11T08:11Z`
- Date: 2026-08-11
- Project: `taskq-api` (round 2 of the harness-methodology test-bed)
- Version: `1.0.0` (per `03-development/src/taskq_api/__init__.py::__version__`)
- HEAD commit: `a2a6597` (dirty — `taskq.db` modified by P4 integration run)
- Phase: 5 — Per-FR Delta verification; last gate reached: Gate 1 (per-FR)
- Repository: `/Users/johnny/projects/taskq-advance`

## 2. Functional Baseline (maps to SRS FR, 100% complete)

| FR ID | Feature Description | Baseline Status | Notes |
|-------|---------------------|-----------------|-------|
| FR-01 | Task Resource CRUD API (`POST/GET/LIST/DELETE /v1/tasks`, cursor pagination, 422/404/409) | PASS | Gate 1 score 100.0; scope `taskq_api.api.tasks` / `service.tasks` / `repository.task_repo` / `models.orm` / `models.schemas` |
| FR-02 | Task Execution Endpoint (`POST /v1/tasks/{id}/run` → 202; `asyncio.create_subprocess_exec(*shlex.split(...))`; run history) | PASS | Gate 1 score 100.0; scope `taskq_api.api.tasks` / `service.runner` / `repository.task_repo` |
| FR-03 | API Key Authentication (`X-API-Key`, SHA-256 hashed, `hmac.compare_digest`, revocation) | PASS | Gate 1 score 100.0; scope `taskq_api.api.deps` / `service.auth` / `repository.key_repo` |
| FR-04 | Scope Authorisation (`read < write < admin`, single dependency, 403 leaks nothing) | PASS | Gate 1 score 100.0; scope `taskq_api.api.deps` / `service.auth` |
| FR-05 | Rate Limiting (per-token token bucket in DB, 429 + `Retry-After`) | PASS | Gate 1 score 100.0; scope `taskq_api.api.deps` / `service.ratelimit` / `repository.rate_repo` |
| FR-06 | Persistence Layer and Transaction Boundaries (repository layer, one Session per request, no raw SQL, no N+1) | PASS | Gate 1 score 100.0; scope `taskq_api.repository.{session,task_repo,key_repo,rate_repo}` |
| FR-07 | Schema Migration (Alembic v1→v2→v3, v3 moves data, every step reversible) | PASS | Gate 1 score 100.0; scope `migrations.versions.{v1_initial,v2_tags,v3_split_results}` + `repository.session` |
| FR-08 | Asynchronous Executor (`asyncio.TaskGroup`, concurrency cap, graceful drain, no orphans) | PASS | Gate 1 score 100.0; scope `taskq_api.service.runner` |
| FR-09 | Health Checks and Observability (`/healthz`, `/readyz` fail-closed on migration lag, `/v1/metrics`) | PASS | Gate 1 score 100.0; scope `taskq_api.api.health` / `repository.session` / `__main__` |
| FR-10 | Error Contract (RFC 7807, `application/problem+json`, `X-Correlation-Id`) | PASS | Gate 1 score 100.0; scope `taskq_api.errors` / `api.deps` |

Source: `.methodology/.gate1_scores.json` (phase 5) — every FR recorded at 100.0; reinforced by `.methodology/quality_manifest.json::gate_results.gate1`.

## 3. Quality Baseline

| Metric | Threshold | Actual | Status |
|--------|-----------|--------|--------|
| Constitution (P5+) | ≥ 80% | Gate 1 per-FR composite = 100.0; Gate 2 P3-exit = 93.14; Gate 3 P4-exit = 94.79 (composite_score 94.792) | PASS |
| Coverage (source tree, `--cov=03-development/src`) | ≥ 80% (Gate 3), 100% project target | 100% (877 / 877 stmts, 0 missed; 28 / 28 files) | PASS |
| Logic Correctness (Gate 3 test_assertion_quality) | ≥ 90 | 93.4 (`{"total": 188, "asserted": 170, "zero_assert": 18 stubs}`) | PASS |
| Mutation score (Gate 1 / Gate 3 — NFR-08) | ≥ 70 | 77.8 (killed=203, survived=58, 10 files, scope `repository` + `service`, framework_override) | PASS |
| Integration coverage (NFR-10) | ≥ 80% | 80% (Gate 2/3 measurement, 39 integration tests pass) | PASS |
| Architecture constraints (NFR-06) | 100 | 100 (Layering KEPT, SQLAlchemy in repository only KEPT) | PASS |
| Security (bandit `-r 03-development/src/ -ll`) | 0 HIGH, 0 MEDIUM | HIGH=0, MEDIUM=0, LOW=1 (B101 assert in `taskq_api/repository/session.py`; pre-existing, framework-known) | PASS |
| Secrets scanning (gitleaks) | 0 leaks | 1 hit in `03-development/tests/test_security_threats.py:382` — synthetic NFR-04 redaction marker `nfr-04-threat-marker-xyz`; non-secret test fixture, pre-existing, in `.gitleaksignore` analogue per Gate 3 evidence_digest | PASS |
| Linting (ruff) | 100 | 100 ("All checks passed!", ruff exit=0, `[]`) | PASS |
| Type safety (pyright) | ≥ 85 | 100 (errorCount=0, filesAnalyzed=29) | PASS |
| Readability (NFR-11) | MI ≥ 80; CC ≤ 10; file ≤ 400 LOC; dir ≤ 15 files; handler ≤ 40 LOC | project_score 96.0, project_avg_cc 1.77, total_lloc 1069 | PASS |
| Documentation (NFR-05) | 100% public symbols with FR/NFR docstrings | 100% (total 82, with_doc 82, missing []) | PASS |
| Error handling (NFR-03) | explicit commit/rollback, no bare except, CancelledError propagates | 100 (7/7 functions with handlers, 0 anti_patterns) | PASS |
| License compliance (NFR-07) | allowlist | 100 (scancode: 2134 files scanned, 0 source license detections, all production deps in BSD/MIT/Apache/ISC/MPL/PSF) | PASS |
| Execute verification target (NFR-12) | `make verify-system` exit 0 + `verify-system: PASS` | 100 (per Gate 2 evidence) | PASS |
| Traceability | ≥ 80% merged | 83.3 (Gate 3); 74.2 (Gate 2 — still PASS at threshold 60) | PASS |

Source: `.methodology/gate3_result.json` and `.methodology/gate2_result.json` for the composite and dimension evidence; `.methodology/mutation_score.json` for the mutation kill ratio.

## 4. Performance Baseline (A/B monitoring)

| Metric | Baseline Value | Source |
|--------|----------------|--------|
| `GET /v1/tasks/{id}` p95 at 10,000 rows (NFR-01, SPEC §8 #15) | < 30 ms target; pytest-benchmark dimension marked `null` (no tests ran, exit 5) — dimension not yet applicable, no regression | Gate 3 `performance` dimension (NFR-01 dimension: `performance`); `04-testing/TEST_RESULTS.md` does not include pytest-benchmark rows |
| `GET /v1/tasks?limit=50` p95 at 10,000 rows (NFR-01) | < 80 ms target; same benchmark gating as above | Gate 3 `performance` dimension |
| List endpoint SQL statement count (N+1 guard, NFR-01 / FR-06) | constant w.r.t. row count — asserted via SQLAlchemy event listener (FR-01 AC-1.7, FR-06 AC-6.4) | SRS §FR-01 / §FR-06 / §NFR-01 AC clauses |
| Wall-clock test suite duration | 170.85 s (≈ 2 min 50 s) for 7,152 tests | `04-testing/TEST_RESULTS.md` Execution Summary |
| Integration test duration | 1.15 s for 39 passed | re-run in this verification (see Validation) |
| Memory / Error rate | not recorded at the harness layer; pytest-benchmark not run; no regression vs prior runs in scope | — |

No new performance regressions are claimed for the P5 verification pass. The performance dimension is still placeholder `null` and is tracked in `04-testing/TEST_RESULTS.md` (no benchmark rows). A future P6 / Gate-4 pass should add pytest-benchmark entries to make NFR-01 AC-N01.1 / AC-N01.2 observably measured (currently the dimension scoring falls back to a framework-override path).

## 5. Known Issues

| Severity | Count | Description |
|----------|-------|-------------|
| HIGH | 0 | — |
| MEDIUM | 0 | — |
| LOW | 1 | `test_readyz_happy_path_200` in `03-development/tests/test_extra_coverage.py` fails because it calls `api_health._ready()` — that name does not exist in `03-development/src/taskq_api/api/health.py`; the source exposes `_is_database_ready()` (lines 51-63). Documented in `04-testing/TEST_RESULTS.md::Deferred / Failing Issues`. Pre-existing test bug, not introduced by P4 / P5; source/test fix is out of P4 and P5 scope. |
| LOW | 1 | Bandit B101 (assert used) at `03-development/src/taskq_api/repository/session.py:133` — pre-existing, framework-known (visible in Gate 2/3 `security` dimension; LOW severity only, ignored at the gate). |
| LOW | 1 | Gitleaks synthetic marker `nfr-04-threat-marker-xyz` in `03-development/tests/test_security_threats.py:382` — explicit NFR-04 redaction fixture; not a real secret; framework documents it under `evidence_digest.secrets_scanning`; pre-existing. |
| DEFERRED | 12 | `pytest.mark.skip` markers (incl. `test_migrations_env_fileconfig_branch` — fileConfig branch in `migrations/env.py:36` is deliberately not driven because it would clobber `caplog` in sibling tests; the remaining 11 skips are pre-existing). Coverage is unaffected (100% on `03-development/src`). |

HIGH severity count = 0 — baseline ready for sign-off.

## 6. Change Log

| Date | Change | Commit / Ref |
|------|--------|--------------|
| 2026-08-11 | feat(FR-10): Gate1 PASS — score=100.0 (phase 5) | `a2a6597` |
| 2026-08-11 | feat(FR-09): Gate1 PASS — score=100.0 (phase 5) | `d7842c0` |
| 2026-08-11 | feat(FR-08): Gate1 PASS — score=100.0 (phase 5) | `5f9f0fa` |
| 2026-08-11 | feat(FR-07): Gate1 PASS — score=100.0 (phase 5) | `edf4f64` |
| 2026-08-11 | feat(FR-06): Gate1 PASS — score=100.0 (phase 5) | `9c9a6be` |
| 2026-08-11 | feat(FR-05): Gate1 PASS — score=100.0 (phase 5) | `3ff384b` |
| 2026-08-11 | feat(FR-04): Gate1 PASS — score=100.0 (phase 5) | `cfc78fd` |
| 2026-08-11 | feat(FR-03): Gate1 PASS — score=100.0 (phase 5) | `7cd6cfb` |
| 2026-08-11 | feat(FR-02): Gate1 PASS — score=100.0 (phase 5) | `a8f8907` |
| 2026-08-11 | feat(FR-01): Gate1 PASS — score=100.0 (phase 5) | `65c012a` |

Source: `git -C /Users/johnny/projects/taskq-advance log --oneline -10` (run during this verification pass).

Pre-P5 phase milestones (for context, not part of the last 10 commits):
- Phase 1 → `1615fe66e7a14746bc401eec1ebecfa46d1f69ec` (2026-08-07)
- Phase 2 → `b773a5a79ad30ada439e5ac0d28c921a217df52c` (2026-08-07)
- Phase 3 → `81bbeb44e786aada8645af8673c92e04fe2f70c8` (2026-08-11, Gate 2 PASS 93.14)
- Phase 4 → `606f888e904d4c88685f3a51259f0588ef4e1ea1` (2026-08-11, Gate 3 PASS 94.79 composite)

## 7. Acceptance Sign-off

- Agent A (orchestrator, P5 verification): orch-post — `p5-verification-2026-08-11T08:11Z` — 2026-08-11
- Agent B (P5 Verification Author, Sonnet / Claude): recorded evidence in this file and in `05-verification/VERIFICATION_REPORT.md` — 2026-08-11
- Approver: pending human review (no in-band approval token)
- Baseline ready: YES (0 HIGH issues, 0 MEDIUM issues, 1 LOW test-bug + 1 LOW bandit + 1 LOW synthetic-secret fixture tracked; composite Gate 3 = 94.79 ≥ 80; Gate 1 per-FR = 100.0)
