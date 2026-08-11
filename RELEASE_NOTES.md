# Release Notes

> **Project**: taskq-advance (`taskq-api`, round 2 of the harness-methodology test-bed)
> **Version**: 1.0.0 (per `03-development/src/taskq_api/__init__.py::__version__`)
> **Release Date**: 2026-08-11
> **HEAD commit**: `1b98c93` — `release(P6): Gate4 PASS score=96.0 — pipeline complete`

---

## Summary

This is the first formal release of `taskq-api`. The project has passed all four
gates of the harness-methodology pipeline (Gate 1 / Gate 2 / Gate 3 / Gate 4)
with **0 HIGH and 0 MEDIUM defects** and a **Gate 4 composite score of 95.98**.

Source: `.methodology/quality_manifest.json::gate_results.gate4`
(`overall_score: 95.98`, `quality_complete: true`, `open_critical: 0`, `open_high: 0`).
See `06-quality/QUALITY_REPORT.md` for the full dimension breakdown (15
dimensions, all PASS).

---

## Gate 4 Composite Score

| Source | Value |
|--------|-------|
| `.methodology/quality_manifest.json::gate_results.gate4.overall_score` | **95.98** |
| `.methodology/quality_manifest.json::gate_results.gate4.score` | 95.98 |
| `06-quality/QUALITY_REPORT.md` (auto-generated) | 95.978 / 100 |

Both manifestations of the persistent SoT agree; the manifest field is the
canonical value per `phase6_plan.md v2.12.0`.

### Gate 4 Dimension Breakdown

| Dimension | Score | Threshold | Status |
|-----------|-------|-----------|--------|
| Linting | 100.0 | 90 | PASS |
| Type Safety | 100.0 | 85 | PASS |
| Test Coverage | 100.0 | 80 | PASS |
| Security | 99.0 | 80 | PASS |
| Secrets Scanning | 100.0 | 100 | PASS |
| License Compliance | 100.0 | 100 | PASS |
| Mutation Testing | 77.6 | 70 | PASS |
| Architecture | 91.7 | 80 | PASS |
| Readability | 96.0 | 80 | PASS |
| Error Handling | 100.0 | 80 | PASS |
| Documentation | 100.0 | 75 | PASS |
| Performance | 100.0 | 75 | PASS |
| Integration Coverage | 80.0 | 80 | PASS |
| Test Assertion Quality | 92.2 | 70 | PASS |
| Traceability | 100.0 | 80 | PASS |

Source: `.methodology/quality_manifest.json` + `06-quality/QUALITY_REPORT.md`.

---

## Feature List (Functional Requirements — 10/10 PASS)

All FRs scored 100.0 at Gate 1 per `.methodology/quality_manifest.json::gate_results.gate1`.

| FR ID | Feature | Modules | Gate 1 Score |
|-------|---------|---------|--------------|
| FR-01 | Task Resource CRUD API (`POST/GET/LIST/DELETE /v1/tasks`, cursor pagination, 422/404/409) | `taskq_api.api.tasks`, `taskq_api.service.tasks`, `taskq_api.repository.task_repo`, `taskq_api.models.orm`, `taskq_api.models.schemas` | 100.0 |
| FR-02 | Task Execution Endpoint (`POST /v1/tasks/{id}/run` → 202; `asyncio.create_subprocess_exec(*shlex.split(...))`; run history) | `taskq_api.api.tasks`, `taskq_api.service.runner`, `taskq_api.repository.task_repo` | 100.0 |
| FR-03 | API Key Authentication (`X-API-Key`, SHA-256 hashed, `hmac.compare_digest`, revocation) | `taskq_api.api.deps`, `taskq_api.service.auth`, `taskq_api.repository.key_repo` | 100.0 |
| FR-04 | Scope Authorisation (`read < write < admin`, single dependency, 403 leaks nothing) | `taskq_api.api.deps`, `taskq_api.service.auth` | 100.0 |
| FR-05 | Rate Limiting (per-token token bucket in DB, 429 + `Retry-After`) | `taskq_api.api.deps`, `taskq_api.service.ratelimit`, `taskq_api.repository.rate_repo` | 100.0 |
| FR-06 | Persistence Layer and Transaction Boundaries (repository layer, one Session per request, no raw SQL, no N+1) | `taskq_api.repository.session`, `taskq_api.repository.task_repo`, `taskq_api.repository.key_repo`, `taskq_api.repository.rate_repo` | 100.0 |
| FR-07 | Schema Migration (Alembic v1 → v2 → v3, v3 moves data, every step reversible) | `migrations.versions.{v1_initial,v2_tags,v3_split_results}`, `taskq_api.repository.session` | 100.0 |
| FR-08 | Asynchronous Executor (`asyncio.TaskGroup`, concurrency cap, graceful drain, no orphans) | `taskq_api.service.runner` | 100.0 |
| FR-09 | Health Checks and Observability (`/healthz`, `/readyz` fail-closed on migration lag, `/v1/metrics`) | `taskq_api.api.health`, `taskq_api.repository.session`, `taskq_api.__main__` | 100.0 |
| FR-10 | Error Contract (RFC 7807, `application/problem+json`, `X-Correlation-Id`) | `taskq_api.errors`, `taskq_api.api.deps` | 100.0 |

---

## Changes Since Prior Release

This is the first formal release of `taskq-api`; there is no prior version to
diff against. The full pipeline history (Phase 1 → Phase 6) is summarised
below, with the most recent commits on top.

### Release commit (P6, Gate 4 exit)
- `1b98c93` — release(P6): Gate4 PASS score=96.0 — pipeline complete
  *(subject verified against `git log --format='%H %h %s'`)*
- `3b90c3b` — handover: advance to Phase 6

### Phase 5 (P5 — Per-FR Delta verification)
- `91b7b8e` — docs(P5): BASELINE.md — review baseline checkpoint
- `3f47a7f` — chore(p5): baseline + verification-report artifacts
- `a2a6597` — feat(FR-10): Gate1 PASS — score=100.0 [phase=5]
- `d7842c0` — feat(FR-09): Gate1 PASS — score=100.0 [phase=5]
- `5f9f0fa` — feat(FR-08): Gate1 PASS — score=100.0 [phase=5]
- `edf4f64` — feat(FR-07): Gate1 PASS — score=100.0 [phase=5]
- `9c9a6be` — feat(FR-06): Gate1 PASS — score=100.0 [phase=5]
- `3ff384b` — feat(FR-05): Gate1 PASS — score=100.0 [phase=5]
- `cfc78fd` — feat(FR-04): Gate1 PASS — score=100.0 [phase=5]
- `7cd6cfb` — feat(FR-03): Gate1 PASS — score=100.0 [phase=5]
- `a8f8907` — feat(FR-02): Gate1 PASS — score=100.0 [phase=5]
- `65c012a` — feat(FR-01): Gate1 PASS — score=100.0 [phase=5]

### Phase 4 (Gate 3 exit — score 94.79)
- `606f888e` — handover: advance to Phase 5
- `cf99fd3` — feat(P4-pre-gate3): all 10 FR(s) Gate1 re-eval PASS; ready for Gate 3
- `e8c2cd9` — test(P4): Gate3 PASS score=94.8 — full test suite
  *(commit subject verified — this is the Gate 3 exit commit; score 94.8 is
  the commit-message abbreviation, the persistent SoT value is 94.79 in
  `.methodology/quality_manifest.json::gate_results.gate3.overall_score`.)*

### Earlier phase milestones (for context only)
- Phase 1 → `1615fe6` (2026-08-07)
- Phase 2 → `b773a5a` (2026-08-07)
- Phase 3 → `81bbeb4` (2026-08-11, Gate 2 PASS 93.14) — `handover: advance to Phase 4`
- Phase 4 → `606f888e` (2026-08-11) — `handover: advance to Phase 5`

All hashes above were verified against `git log --format='%H %s` against the
actual commit subjects before being included.

---

## Quality Evidence Summary

| Metric | Value | Source |
|--------|-------|--------|
| Gate 4 composite | 95.98 | `.methodology/quality_manifest.json::gate_results.gate4` |
| Source line coverage | 100% (882 / 882 stmts, 28 source files) | `06-quality/QUALITY_REPORT.md`, `.methodology/gate4_result.json::breakdown.test_coverage` |
| Branch coverage | 100% | `.methodology/gate4_result.json::breakdown.test_coverage.tool_evidence` |
| Mutation score | 77.6 (killed=204, survived=59, 10 files, scope = repository + service) | `.methodology/mutation_score.json` (read 2026-08-11) |
| Linting (ruff) | 0 violations | `.methodology/gate4_result.json::breakdown.linting` |
| Type safety (pyright) | 29 files, errorCount=0 | `.methodology/gate4_result.json::breakdown.type_safety` |
| Security (bandit -ll) | 0 HIGH, 0 MEDIUM, 1 LOW (B101 at `repository/session.py:136`) | `.methodology/gate4_result.json::breakdown.security` |
| Secrets (gitleaks) | 0 leaks in 127 commits | `.methodology/gate4_result.json::breakdown.secrets_scanning` |
| License (scancode) | 2012 files / 122 dirs scanned, errors=0, no copyleft-tainted deps | `.methodology/gate4_result.json::breakdown.license_compliance` |
| Integration coverage (NFR-10) | 80%, 39 / 39 integration tests pass via httpx ASGITransport | `.methodology/gate3_result.json`, `05-verification/VERIFICATION_REPORT.md::§2.1` |
| Architecture (CRG) | 91.7 (drift vs P4 baseline = 0.00) | `.methodology/gate4_result.json::breakdown.architecture` |
| Readability | project_score 96.0, avg CC 1.77, total LLOC 1068 | `.methodology/gate4_result.json::breakdown.readability` |
| Documentation | 82 / 82 public symbols with FR/NFR docstrings | `.methodology/quality_manifest.json::nfr_traceability.NFR-05` |

---

## Known Limitations

### Disclosed in the Quality Report (LOW severity only)

| Area | Finding | Severity |
|------|---------|----------|
| Security | Bandit `B101 assert used` at `03-development/src/taskq_api/repository/session.py:136` (assert documenting `get_engine()` sessionmaker-pair invariant). Tests run without `-O` so the assert is live in CI. Pre-existing LOW; not a regression. | LOW |
| Architecture | CRG surfaced 12 dead-code-candidate symbols (FastAPI handlers + framework callbacks + test helpers). All are framework entrypoints / `api/health.py::healthz|readyz`, `api/metrics.py::get_metrics`, `app.py::_correlation_id_middleware|_problem_handler|_validation_handler|_unhandled_exception_handler`, `models/orm.py::_new_uuid`, `service/runner.py::_drop`, and `tests/conftest.py::{anyio_backend,sqlite_db_url}`. Advisory only — verified as live framework callbacks/entrypoints before any removal. | advisory |

### Pre-existing baseline items (LOW)

| Area | Finding | Source |
|------|---------|--------|
| Test code | `test_readyz_happy_path_200` in `03-development/tests/test_extra_coverage.py` calls `api_health._ready()` which does not exist; source exposes `_is_database_ready()`. Pre-existing test bug; out of P5 / P6 scope. | `05-verification/BASELINE.md::§5` |
| Test code | 12 `pytest.mark.skip` markers (incl. `test_migrations_env_fileconfig_branch`). Coverage unaffected (100% on source tree). | `05-verification/BASELINE.md::§5` |

### Honest disclosures (no fabrication)

- **No pytest-benchmark rows were added at P6 / Gate 4.** The `performance`
  dimension scores 100.0 at Gate 4 via a framework-override path (see
  `.methodology/quality_manifest.json::gate_score_overrides.performance = 75.0`
  and `.methodology/gate4_result.json::breakdown.performance`). NFR-01 AC-N01.1
  / AC-N01.2 (`GET /v1/tasks/{id}` p95 < 30 ms, `GET /v1/tasks?limit=50` p95 <
  80 ms at 10,000 rows) remain unverified by direct measurement. See
  `05-verification/VERIFICATION_REPORT.md::§2.5` and `05-verification/BASELINE.md::§4`.
- **No git tag has been created for this release.** The tag `gate4-20260811-score95`
  exists but is the gate marker, not a release tag. `git tag` creation is
  out of scope for this deliverable.
- **No mutation re-run at P5 / P6** beyond the cached `.methodology/mutation_score.json`
  value 77.6 (read 2026-08-11T08:33:41Z). `05-verification/VERIFICATION_REPORT.md::§2.4`
  explicitly states P5 does NOT re-run mutation testing per scope.

---

## Defect Summary

| Severity | Count |
|----------|-------|
| Critical | 0 |
| High | 0 |
| Medium | 0 |
| Low | 0 (per `06-quality/QUALITY_REPORT.md::Defect Summary`) |

The 1 LOW bandit B101 is recorded as an `issue` under the `security` dimension
in `06-quality/QUALITY_REPORT.md` (scored 99.0, still PASS) — it is the same
LOW finding carried forward from Gate 2 / Gate 3, not a new defect.

---

## Provenance

- Quality Report (canonical SoT): `06-quality/QUALITY_REPORT.md` (auto-generated
  by `harness/scripts/generate_quality_report.py`, 2026-08-11 08:55:55 UTC).
- Persistent manifest: `.methodology/quality_manifest.json` (Gate 4 block).
- Gate result detail: `.methodology/gate4_result.json` (per-dimension breakdown).
- Verification report: `05-verification/VERIFICATION_REPORT.md`.
- Baseline: `05-verification/BASELINE.md`.
- Risk register: `07-risk/RISK_REGISTER.md`.
- All commit hashes verified against `git log --format='%H %s'`.

---

_Generated by P6 Release Author._