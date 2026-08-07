# Traceability Matrix — taskq-api

> Bidirectional FR ⇄ SRS ⇄ Code ⇄ Test traceability for `taskq-api`.
> Framework: harness-methodology · Round 2 · Phase 1 · v1.0.0
> Canonical spec: `SPEC.md` (verbatim transcription via `SRS.md`, per `R-CANONICAL-INTERP-001`)
> Source of truth for Status column: `harness/scripts/build_traceability.py`
> (imported as `scripts.build_traceability` by `harness/cli/phase_cmds.py`,
> machine-refreshed on `advance-phase`).

---

## 1. Overview

Provides complete **FR ↔ SRS ↔ SAD Design Element ↔ Code ↔ Test** bidirectional
traceability supporting ASPICE SWE.3 / SYS.4 compliance. Each Functional
Requirement (FR-01..FR-10) and Non-Functional Requirement (NFR-01..NFR-12)
is linked to:

1. its canonical SRS clause and Acceptance Criterion (AC),
2. the SPEC.md §8 verification row that proves it (or `SPEC.md` §10 dimension
   for framework-enforced NFRs without an §8 command — see NFR-05, NFR-11),
3. the **SAD Design Element** (`SAD-DE-XX`) it owns — materialised in
   `02-architecture/SAD.md` §2 in Phase 2, providing the FR → design element
   → test forward chain,
4. the implementation module that owns it,
5. the test (unit / integration / cross-cutting) that exercises it.

Status legend (mirror of `SPEC_TRACKING.md`):

| Status | Meaning |
|--------|---------|
| DRAFT | Phase 1 — text-only; no code/test yet. Default starting value. |
| IN_PROGRESS | Code/module exists; tests not yet green. |
| Implemented | Code exists; awaiting machine-refresh. |
| Verified | Code + tests exist and pass (machine-refreshed). |
| Deferred | Explicitly deferred (see `SRS.md` §7). |

---

## 2. FR ↔ SRS Mapping

The 10 functional requirements (`SPEC.md` §3, transcribed verbatim into
`SRS.md` §3) and the binding acceptance criterion per FR:

| FR ID | Functional Requirement | SRS §  | AC Count | SPEC §8 row(s) | Priority | Status |
|-------|------------------------|--------|----------|----------------|----------|--------|
| FR-01 | Task Resource CRUD API (`POST/GET/LIST/DELETE /v1/tasks`, cursor pagination, 422/404/409) | `SRS.md` §3 FR-01 | 7 (AC-1.1..AC-1.7) | #4, #5, #7, #8, #14 | HIGH | DRAFT |
| FR-02 | Task Execution Endpoint (`POST /v1/tasks/{id}/run` → 202; `asyncio.create_subprocess_exec`; run history) | `SRS.md` §3 FR-02 | 5 (AC-2.1..AC-2.5) | #16, #25 | HIGH | DRAFT |
| FR-03 | API Key Authentication (`X-API-Key`, SHA-256 hashed, `hmac.compare_digest`, revocation) | `SRS.md` §3 FR-03 | 5 (AC-3.1..AC-3.5) | #5, #18 | HIGH | DRAFT |
| FR-04 | Scope Authorisation (`read < write < admin`, single dependency, 403 leaks nothing) | `SRS.md` §3 FR-04 | 3 (AC-4.1..AC-4.3) | #6 | HIGH | DRAFT |
| FR-05 | Rate Limiting (per-token token bucket in DB, 429 + `Retry-After`, exempt healthz/readyz) | `SRS.md` §3 FR-05 | 3 (AC-5.1..AC-5.3) | #9 | HIGH | DRAFT |
| FR-06 | Persistence Layer & Tx Boundaries (`repository/` only, 1 Session/req, no raw SQL, no N+1) | `SRS.md` §3 FR-06 | 5 (AC-6.1..AC-6.5) | #14, #17 | HIGH | DRAFT |
| FR-07 | Schema Migration (Alembic v1→v2→v3, v3 moves data, every step reversible, round-trip byte-identical) | `SRS.md` §3 FR-07 | 5 (AC-7.1..AC-7.5) | #12, #13 | HIGH | DRAFT |
| FR-08 | Asynchronous Executor (`asyncio.TaskGroup`, concurrency cap, graceful drain, no orphans) | `SRS.md` §3 FR-08 | 4 (AC-8.1..AC-8.4) | #25 | HIGH | DRAFT |
| FR-09 | Health Checks & Observability (`/healthz`, `/readyz` fail-closed on migration lag, `/v1/metrics`) | `SRS.md` §3 FR-09 | 5 (AC-9.1..AC-9.5) | #10, #11 | HIGH | DRAFT |
| FR-10 | Error Contract RFC 7807 (`application/problem+json`, correlation-id, status-code mapping) | `SRS.md` §3 FR-10 | 4 (AC-10.1..AC-10.4) | #19 | HIGH | DRAFT |

12 non-functional requirements (`SPEC.md` §4, transcribed verbatim into
`SRS.md` §4):

| NFR ID | Non-Functional Requirement | SRS §  | Dimension | SPEC §8 row(s) | Priority | Status |
|--------|----------------------------|--------|-----------|----------------|----------|--------|
| NFR-01 | Performance & Query Efficiency (p95 < 30 ms / < 80 ms at 10k rows; constant SQL count) | `SRS.md` §4 NFR-01 | `performance` | #14, #15 | HIGH | DRAFT |
| NFR-02 | HTTP & Data-Layer Security (no shell/eval/exec; no string-concatenated SQL; hashed keys; CORS deny; 0 bandit HIGH/MED) | `SRS.md` §4 NFR-02 | `security` | #6, #16, #17, #18, #19, #23 | HIGH | DRAFT |
| NFR-03 | Error Handling, Tx, Async Correctness (explicit tx; no bare except; CancelledError propagates; timeouts kill children) | `SRS.md` §4 NFR-03 | `error_handling` | #25 | HIGH | DRAFT |
| NFR-04 | Sensitive Data Redaction (stdout/stderr/log/error regex redaction; DB URL never in log; key plaintext once) | `SRS.md` §4 NFR-04 | `security` | #20 | HIGH | DRAFT |
| NFR-05 | Documentation Coverage (100% public docstrings reference `[FR-XX]`/`[NFR-XX]`; `/openapi.json` complete) | `SRS.md` §4 NFR-05 | `documentation` | §10 dim `ast-docstrings` | MEDIUM | DRAFT |
| NFR-06 | Architecture Layering Contract (`.importlinter` layers `api > service > repository > models`; sqlalchemy forbidden outside repository) | `SRS.md` §4 NFR-06 | `architecture_constraints` | #21 | HIGH | DRAFT |
| NFR-07 | Dependency & License Compliance (== pin + lock; allowlist MIT/BSD-2/BSD-3/Apache-2.0/PSF; whole-tree scan; SBOM at `08-config/SBOM.json`) | `SRS.md` §4 NFR-07 | `license_compliance` | #22 | MEDIUM | DRAFT |
| NFR-08 | Mutation Testing (`features.mutation_testing: true`; `mutmut run` ≥ 70 over service/ + repository/) | `SRS.md` §4 NFR-08 | `mutation_testing` | #24 | MEDIUM | DRAFT |
| NFR-09 | Verification Honesty / Zero-Skip Rule (0 pytest skip/skipif/xfail; no assertion-free stubs; migration against real DB file) | `SRS.md` §4 NFR-09 | `test_assertion_quality` | #1, #12 | HIGH | DRAFT |
| NFR-10 | Integration Coverage (`tests/integration/` ≥ 80% line cov; `httpx.ASGITransport`; every error code covered) | `SRS.md` §4 NFR-10 | `integration_coverage` | #3 | HIGH | DRAFT |
| NFR-11 | Readability (MI ≥ 80; CC ≤ 10; ≤ 400 lines/file; ≤ 15 files/dir; ≤ 40 lines per API handler) | `SRS.md` §4 NFR-11 | `readability` | §10 dim `readability-v2` | MEDIUM | DRAFT |
| NFR-12 | System Verification Target (`make verify-system` exits 0; prints `verify-system: PASS`) | `SRS.md` §4 NFR-12 | `execute_verification_target` | #27 | HIGH | DRAFT |

**Coverage rule**: every row above must have non-empty SPEC §8 row(s) and
a non-`DRAFT` Status before `advance-phase` may mark Phase 1 complete.

---

## 3. SRS ↔ Code Mapping

The implementation module that owns each FR/NFR (per
`SPEC_TRACKING.md` §5 module ownership, cross-checked against
`SRS.md` §10 implementation_functions). The **SAD Design Element** column
is the P2 forward reference: each `SAD-DE-XX` ID will be materialised in
`02-architecture/SAD.md` §2 Module Design in Phase 2, then resolved to a
real `03-development/src/` file path by `build_traceability.py` in Phase 4.

| SRS Clause | FR/NFR | Code Module | SAD Design Element | Co-owner | Notes |
|------------|--------|-------------|--------------------|----------|-------|
| §3 FR-01 | FR-01 | `taskq_api.api.tasks` | `SAD-DE-TASKS-CRUD` | `taskq_api.service.tasks`, `taskq_api.repository.task_repo` | CRUD handler + paged-list repo with `selectinload`. |
| §3 FR-02 | FR-02 | `taskq_api.service.runner` | `SAD-DE-RUNNER-EXEC` | `taskq_api.api.tasks` | Subprocess invocation + run history endpoint. |
| §3 FR-03 | FR-03 | `taskq_api.service.auth` | `SAD-DE-AUTH-KEY` | `taskq_api.repository.key_repo` | Single dependency in `taskq_api.api.deps`. |
| §3 FR-04 | FR-04 | `taskq_api.api.deps` | `SAD-DE-DEPS-AUTHZ` | — | Single auth/authz decision point; route-coverage test invariant. |
| §3 FR-05 | FR-05 | `taskq_api.service.ratelimit` | `SAD-DE-RATELIMIT-BUCKET` | `taskq_api.repository.rate_repo` | DB-persisted bucket + row-level lock (NFR-99-01). |
| §3 FR-06 | FR-06 | `taskq_api.repository.session` | `SAD-DE-REPO-SESSION` | `taskq_api.repository.task_repo` | Session-per-request context manager; `pool_pre_ping=True`. |
| §3 FR-07 | FR-07 | `migrations/versions/v1_initial`, `migrations/versions/v2_tags`, `migrations/versions/v3_split_results` | `SAD-DE-MIGRATIONS-CHAIN` | — | Round-trip reversibility is the load-bearing invariant. |
| §3 FR-08 | FR-08 | `taskq_api.service.runner` | `SAD-DE-RUNNER-TASKGROUP` | — | TaskGroup + drain + timeout kill sequence. |
| §3 FR-09 | FR-09 | `taskq_api.api.health` | `SAD-DE-HEALTH-READY` | — | `/healthz`, `/readyz`, `/v1/metrics`. |
| §3 FR-10 | FR-10 | `taskq_api.errors` | `SAD-DE-ERROR-PROBLEM` | `taskq_api.api.deps` | RFC 7807 problem+json + correlation-id. |
| §4 NFR-01 | NFR-01 | `taskq_api.repository.task_repo` | `SAD-DE-NFR-PERF` | `taskq_api.api.tasks` | `selectinload`/`joinedload` + event-listener count. |
| §4 NFR-02 | NFR-02 | cross-cutting | `SAD-DE-NFR-SEC` | — | grep + bandit + integration; enforced via `harness/SKILL.md`. |
| §4 NFR-03 | NFR-03 | cross-cutting | `SAD-DE-NFR-ERR` | `taskq_api.repository.session`, `taskq_api.service.runner` | ast-error-handling + integration. |
| §4 NFR-04 | NFR-04 | `taskq_api.errors` | `SAD-DE-NFR-REDACT` | `taskq_api.service.runner`, `taskq_api.config` | Redaction regex applied to stdout/stderr/log/error. |
| §4 NFR-05 | NFR-05 | cross-cutting | `SAD-DE-NFR-DOC` | — | ast-docstring scan + openapi.json assertion. |
| §4 NFR-06 | NFR-06 | `.importlinter` (project root) | `SAD-DE-NFR-LAYER` | — | `lint-imports` gate. |
| §4 NFR-07 | NFR-07 | `requirements.txt`, `requirements.lock`, `08-config/SBOM.json` | `SAD-DE-NFR-LICENSE` | — | `pip-licenses --with-system`. |
| §4 NFR-08 | NFR-08 | `service/`, `repository/` (scope) | `SAD-DE-NFR-MUTATION` | `.methodology/harness_config.json` | `mutmut run` ≥ 70. |
| §4 NFR-09 | NFR-09 | `03-development/tests/` | `SAD-DE-NFR-ZEROSKIP` | — | Zero-skip pytest collector scan. |
| §4 NFR-10 | NFR-10 | `03-development/tests/integration/` | `SAD-DE-NFR-INTEGCOV` | — | `httpx.ASGITransport`. |
| §4 NFR-11 | NFR-11 | cross-cutting | `SAD-DE-NFR-READABILITY` | — | readability-v2 thresholds. |
| §4 NFR-12 | NFR-12 | `Makefile` | `SAD-DE-NFR-VERIFYTARGET` | — | `verify-system` chain. |

Code modules do not yet exist on disk (Phase 1 only); these rows are the
*contracted* module ownership that Phase 2 (`02-architecture/`) will lock
into `SAD.md` design elements, then Phase 3 (`03-development/src/`) will
materialise. Verification of this section is deferred to Phase 4 (`04-testing/`)
when `build_traceability.py` resolves each `implementation_functions` token to
a real file path.

---

## 4. Code ↔ Test Mapping

Each FR/NFR's expected test name (per the P1 naming authority
`TEST_INVENTORY.yaml`, expanded by `derive_test_cases.md` per AC). Tests
fall in three buckets — `unit/`, `integration/`, `cross_cutting/` — and
each name MUST appear in `03-development/tests/` before its Status flips
from `DRAFT` to `Implemented`.

| FR/NFR | Code Module | Unit Test(s) | Integration Test(s) | Cross-cutting Test(s) | SPEC §8 row |
|--------|-------------|--------------|----------------------|----------------------|-------------|
| FR-01 | `taskq_api.api.tasks` | `test_fr01_example_unit` (TEST_INVENTORY.yaml authority), `test_create_task_unit`, `test_task_pydantic_validation_unit`, `test_cursor_pagination_unit` | `test_fr01_example_integration` (TEST_INVENTORY.yaml authority), `test_create_task_returns_201`, `test_get_unknown_returns_404`, `test_duplicate_name_returns_409`, `test_list_limit_exceeds_max_returns_422`, `test_list_sql_count_constant` | — | #4, #7, #8, #14 |
| FR-02 | `taskq_api.service.runner` | `test_subprocess_invoke_unit`, `test_state_machine_transitions_unit`, `test_shlex_split_injection_unit` | `test_run_returns_202_with_run_id`, `test_runs_history_newest_first`, `test_task_results_persisted` | `test_timeout_kills_child_no_orphan`, `test_graceful_drain` | #25 |
| FR-03 | `taskq_api.service.auth` | `test_sha256_hash_unit`, `test_hmac_compare_digest_unit`, `test_revoked_key_rejected_unit`, `test_key_create_prints_once_unit` | `test_missing_api_key_returns_401`, `test_invalid_key_returns_401` | — | #5, #18 |
| FR-04 | `taskq_api.api.deps` | `test_scope_hierarchy_unit`, `test_single_dependency_routes_unit` | `test_write_key_cannot_delete_returns_403`, `test_403_body_does_not_reveal_existence` | `test_all_v1_routes_use_same_dep` | #6 |
| FR-05 | `taskq_api.service.ratelimit` | `test_token_bucket_refill_unit`, `test_row_level_lock_unit` | `test_exceed_burst_returns_429_with_retry_after`, `test_healthz_readyz_not_rate_limited` | — | #9 |
| FR-06 | `taskq_api.repository.session` | `test_context_manager_commit_unit`, `test_context_manager_rollback_unit`, `test_pool_pre_ping_unit` | `test_repository_only_owns_session`, `test_list_endpoint_constant_sql_count`, `test_no_string_concatenated_sql_grep` | `test_sqlalchemy_import_outside_repository_blocked` | #14, #17 |
| FR-07 | `migrations/versions/v*` | `test_revision_chain_unit` | `test_upgrade_head_succeeds`, `test_downgrade_base_leaves_no_residual`, `test_round_trip_byte_identical`, `test_no_drop_table_shortcut` | `test_migration_offline_sql_render` | #12, #13 |
| FR-08 | `taskq_api.service.runner` | `test_taskgroup_unit`, `test_concurrency_cap_unit`, `test_drain_unit` | `test_max_concurrent_enforced`, `test_drain_timeout_marks_interrupted` | `test_cancelled_error_propagates`, `test_timeout_terminates_child` | #25 |
| FR-09 | `taskq_api.api.health` | `test_readyz_db_check_unit`, `test_readyz_alembic_head_check_unit` | `test_readyz_503_when_db_unreachable`, `test_readyz_503_when_migration_lag`, `test_metrics_requires_admin` | — | #10, #11 |
| FR-10 | `taskq_api.errors` | `test_problem_json_fields_unit`, `test_correlation_id_propagates_unit`, `test_status_code_mapping_unit` | `test_500_body_no_stack_or_path`, `test_correlation_id_matches_header_and_log` | — | #19 |
| NFR-01 | `taskq_api.repository.task_repo` | `test_selectinload_unit` | `test_get_task_p95_under_30ms_at_10k_rows`, `test_list_p95_under_80ms_at_10k_rows`, `test_sql_count_constant_via_event_listener` | — | #14, #15 |
| NFR-02 | cross-cutting | — | `test_403_body_no_resource_leak`, `test_cors_denies_default_origin` | `test_grep_shell_eval_exec_zero_hits`, `test_grep_string_concat_sql_zero_hits`, `test_api_keys_table_no_plaintext`, `test_500_body_no_leak`, `test_bandit_zero_high_medium` | #16, #17, #18, #19, #23 |
| NFR-03 | cross-cutting | `test_no_bare_except_unit`, `test_cancelled_error_propagates_unit` | `test_transaction_rollback_on_exception`, `test_db_failure_readyz_503`, `test_migration_failure_rolls_back` | `test_timeout_terminates_child`, `test_graceful_drain` | #25 |
| NFR-04 | `taskq_api.errors` | `test_redaction_regex_unit`, `test_db_url_password_not_logged_unit` | `test_redaction_applied_to_stdout_stderr` | `test_logs_and_metrics_no_db_password`, `test_key_plaintext_printed_once` | #20 |
| NFR-05 | cross-cutting | `test_docstring_coverage_unit`, `test_openapi_summary_description_unit` | `test_openapi_json_complete` | — | §10 dim `ast-docstrings` |
| NFR-06 | `.importlinter` | — | — | `test_lint_imports_exit_zero`, `test_sqlalchemy_forbidden_outside_repository` | #21 |
| NFR-07 | `requirements.txt` etc. | — | — | `test_requirements_pinned_with_eq`, `test_requirements_lock_full`, `test_licenses_in_allowlist`, `test_sbom_present` | #22 |
| NFR-08 | `service/`, `repository/` | — | — | `test_harness_config_mutation_testing_enabled`, `test_mutmut_score_geq_70` | #24 |
| NFR-09 | `03-development/tests/` | — | — | `test_zero_skipped`, `test_zero_xfail`, `test_zero_assertion_free`, `test_no_collect_ignore`, `test_no_deselect_or_k_filter`, `test_migration_real_db_not_in_memory` | #1, #12 |
| NFR-10 | `03-development/tests/integration/` | — | `test_full_crud_chain`, `test_one_example_each_401_403_404_409_422_429_503`, `test_migration_round_trip_integration`, `test_rate_limit_trigger_and_recovery`, `test_graceful_drain_integration` | — | #3 |
| NFR-11 | cross-cutting | — | — | `test_mi_geq_80`, `test_cc_leq_10`, `test_file_lines_leq_400`, `test_dir_files_leq_15`, `test_api_handler_leq_40_lines` | §10 dim `readability-v2` |
| NFR-12 | `Makefile` | — | — | `test_verify_system_exits_zero`, `test_verify_system_prints_pass` | #27 |

> Test names listed above are the *naming authority*. Where
> `TEST_INVENTORY.yaml` already specifies a name (e.g. `test_fr01_example_unit`),
> that name is preserved; the rows above are filled in by `derive_test_cases.md`
> in Phase 2 per the 7-Question Protocol and become authoritative in `TEST_SPEC.md`.

---

## 5. Completeness Verification

The matrix is *complete* when every row above has a non-empty Status and
no `DRAFT` row remains. Verification commands (each row corresponds to
one ASPICE SWE.3.B.SP check):

| # | Check | Target | Method | Source | Status |
|---|-------|--------|--------|--------|--------|
| 1 | FR → SRS mapping | 22 / 22 (10 FR + 12 NFR) | Section §2 row count | `SRS.md` §3 + §4 | VERIFIED (text-only, machine-refresh on Phase 2) |
| 2 | SRS → Code mapping | 22 / 22 owned | Section §3 row count vs `SPEC_TRACKING.md` §5 | `SPEC_TRACKING.md` §5 | DRAFT (code modules not yet authored) |
| 3 | Code → Test mapping | every FR/NFR has ≥ 1 test | Section §4 row coverage | `TEST_INVENTORY.yaml` + `derive_test_cases.md` | DRAFT (tests not yet authored) |
| 4 | Test coverage (line) | TOTAL 100% (`03-development/src/`) | `pytest 03-development/tests --cov=03-development/src --cov-report=term` | `SPEC.md` §8 #2 | DRAFT |
| 5 | Integration coverage | ≥ 80% (`03-development/tests/integration/`) | `pytest 03-development/tests/integration --cov=03-development/src --cov-report=term` | `SPEC.md` §8 #3 | DRAFT |
| 6 | Mutation score | ≥ 70 over `service/` + `repository/` | `mutmut run` + `mutmut results` | `SPEC.md` §8 #24 | DRAFT |
| 7 | Skipped tests | = 0 | `pytest 03-development/tests -q` shows `skipped = 0` | `SPEC.md` §8 #1 / NFR-09 | DRAFT |
| 8 | Bandit | 0 HIGH, 0 MEDIUM | `bandit -r 03-development/src/` | `SPEC.md` §8 #23 | DRAFT |
| 9 | License allowlist | 100% deps MIT/BSD-2/BSD-3/Apache-2.0/PSF | `pip-licenses --format=json --with-system` | `SPEC.md` §8 #22 | DRAFT |
| 10 | `make verify-system` | exit 0, stdout `verify-system: PASS` | `make verify-system` | `SPEC.md` §8 #27 / NFR-12 | DRAFT |
| 11 | `lint-imports` | exit 0 | `lint-imports` | `SPEC.md` §8 #21 / NFR-06 | DRAFT |
| 12 | `crg_cohesion_healthy` | retained at default | `crg_cohesion_healthy` score unchanged | `SPEC.md` §10 | DRAFT |

A `VERIFIED` flag in this matrix is only set after the corresponding test
actually runs and passes (per `NFR-09` AC-N09.6, `SPEC.md` §4 NFR-09).
`advance-phase` runs `build_traceability.py` → `spec_tracking_render.refresh_status_table`
and overwrites every Status cell in-place from the live code/test scan; a
hand-edit to `Verified` without backing code + tests is silently reverted.

---

## 6. ASPICE Compliance

| ASPICE Capability | Evidence in this matrix | Status |
|-------------------|--------------------------|--------|
| **SWE.3.B.SP1** Task-to-work-product traceability | Section §2 (FR → SRS), §3 (SRS → Code) | DRAFT — text-only this round; verified when §3 rows point at real `03-development/src/` files in Phase 2 |
| **SWE.3.B.SP2** Bidirectional traceability | Each row is reachable forward (FR→SRS→Code→Test) and reverse (Test→FR) | DRAFT — reverse path verified when `TEST_INVENTORY.yaml` is fully populated in Phase 2 |
| **SWE.3.B.SP3** Traceability consistency | `SPEC_TRACKING.md` (Status), `SRS.md` (FR/NFR text), this matrix all reference the same `SPEC.md` clauses | VERIFIED (text-only, machine-refresh on Phase 2) |
| **SWE.3.B.SP4** Traceability change control | Status Legend + Status column overwrites only via `advance-phase` | VERIFIED (process gate) |

---

## 7. Phase 1 → Phase 2 Hand-off

Before Phase 2 (`02-architecture/`) may begin:

- Every row in §2 has a non-empty `SPEC §8 row(s)` cell (currently: 22 / 22;
  NFR-05 and NFR-11 cite `SPEC.md` §10 framework dimensions, since they are
  dimension-enforced rather than command-enforced — see column header).
- Every row in §3 carries a `SAD-DE-XX` design-element identifier AND a code
  module that exists in `SPEC_TRACKING.md` §5 Module Ownership (currently:
  22 / 22, mirrored). Phase 2 will materialise each `SAD-DE-XX` into a real
  `02-architecture/SAD.md` §2 module entry, then Phase 3 will turn those into
  `03-development/src/` files.
- The `DRAFT` Status values in §3 / §4 will be flipped to `Implemented`
  by `build_traceability.py` once the corresponding `03-development/src/`
  files exist and tests are collected.

Out of scope (carried over from `SRS.md` §6):

- TypeScript service — round 3, deferred.
- `taskq-plus` CLI — round 1, already delivered.
- Horizontal-scaling implementation beyond per-token token bucket + `pool_pre_ping`.

---

## 8. References

- `SPEC.md` — canonical source (v1.0.0, 2026-07-30), `PROJECT_BRIEF.md` `canonical_spec`.
- `SRS.md` — verbatim FR/NFR transcription per `R-CANONICAL-INTERP-001`.
- `SPEC_TRACKING.md` — module ownership + machine-refreshed Status source.
- `TEST_INVENTORY.yaml` — P1 naming authority for test function names.
- `harness/scripts/build_traceability.py` — Status refresh on `advance-phase`
  (imported by `harness/cli/phase_cmds.py` as `scripts.build_traceability`).
- `harness/harness/ssi/prompts/evaluate_dimension.md` — current `dimension` ranking.
- ASPICE SWE.3 / SYS.4 — process capability baseline this matrix supports.
