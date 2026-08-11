# RISK_REGISTER.md — taskq-advance

> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-11
> **Framework**: harness-methodology v2.12.0
> **Entry state**: Gate 4 PASS (95.98), Gate 1 PASS 10/10 FRs, Gate 2 93.14, Gate 3 94.79

---

## 1. Scope and Evidence Provenance

Every risk below is traced to a named artifact. Nothing in this register is inferred
without a source file.

| Source | Path | Status |
|---|---|---|
| SPEC risk matrix §9 (R1–R12) | `SPEC.md:441–455` | ✅ present — 12 seeded risks |
| Gate 3 result | `.methodology/gate3_result.json` | ✅ present — 16 dimensions |
| Gate 4 result | `.methodology/gate4_result.json` | ✅ present — 14 dimensions, 4 dimensions carry `issues[]` |
| Gate 1 per-FR | `.methodology/fr_progress.json`, `.methodology/gate1_result.json` | ✅ present — 10/10 at 100.0 |
| Quality report | `06-quality/QUALITY_REPORT.md` | ✅ present — 0 Critical / 0 High / 0 Medium / 0 Low defects |
| Baseline known issues | `05-verification/BASELINE.md:70–79` | ✅ present — 3 LOW + 12 DEFERRED |
| Mutation survivors | `.methodology/mutation_survivors.json` | ✅ present — 59 survivors |
| Adversarial bug hunt | `.methodology/bug_hunt_report.json` | ✅ present — 12 raw / **0 confirmed** / 12 refuted |
| Orchestration degradations | `.methodology/degradations.jsonl` | ✅ present — 10 TIMEOUT / TURN_BUDGET / INFRA events |
| Deferred fixes list | `.methodology/deferred_fixes.md` | ❌ **absent** — file does not exist in this repo |
| SSI issue registry | `.sessi-work/issue_registry.json` | ❌ **absent** — file does not exist in this repo |

> **Provenance note — seed mismatch (unresolved, flagged not silently corrected).**
> The P7 workflow prompt names the SPEC §9 seed as "R1 concurrent write / R2 subprocess
> hang / R3 breaker deadlock / R4 stale cache". The actual `SPEC.md` §9 matrix in this
> repository is R1 v3 migration data loss / R2 SQL injection / R3 API key leak /
> R4 403 resource-existence leak. **This register seeds from the repository's real
> `SPEC.md`.** The workflow-prompt labels appear to be generic template text and match no
> artifact in this project. Requires human confirmation that no second risk matrix exists.

### Scoring model

Likelihood and impact are 1–5. Two scores are recorded per risk:

- **Inherent** = `SPEC §9` rating as authored before implementation (高=5 / 中=3 / 低=2 impact;
  高=4 / 中=3 / 低=2 likelihood). Project-discovered risks (R13+) have no SPEC rating; their
  inherent likelihood is set from observed frequency in the artifacts.
- **Residual** = likelihood re-rated against *verified* Gate 1–4 evidence, impact unchanged.
  Impact does not decay from testing; only likelihood does.

`Score = Likelihood × Impact`. Band: **HIGH ≥ 9**, **MEDIUM 4–8**, **LOW ≤ 3**.
Formal mitigation plans (`RISK_MITIGATION_PLANS.md`) are mandatory for **residual ≥ 9**.

> Rating residual likelihood from evidence — rather than freezing the SPEC's pre-implementation
> guess — is a deliberate choice. Freezing SPEC ratings would classify 11 of 12 risks HIGH and
> make the HIGH band carry no signal. The inherent column is retained so the original assessment
> stays auditable.

---

## 2. Register — SPEC §9 Seeded Risks (R1–R12)

| ID | Name | Category | Inh. L | Imp. | Inh. | Res. L | **Res.** | Band | Mitigation approach & verifying evidence |
|----|------|----------|-------|------|------|--------|-----------|------|------------------------------------------|
| R1 | v3 migration loses data during column split | data-integrity | 3 | 5 | 15 | 1 | **5** | 🟡 MEDIUM | Round-trip reversibility test compares every column against a real DB (FR-07 / §8 #12). Verified: FR-07 Gate 1 = 100.0; `migrations.versions.v3_split_results` is a declared high-risk module and carries dedicated tests. |
| R2 | SQL injection via string-built queries | security | 2 | 5 | 10 | 1 | **5** | 🟡 MEDIUM | Ban on string concatenation + ORM/parameterised binding + grep gate (NFR-02). Verified: bandit HIGH=0 MEDIUM=0; import-linter contract "SQLAlchemy in repository only" KEPT; bug-hunt T-01 refuted with live repro. |
| R3 | API key leaks from storage or logs | security | 3 | 5 | 15 | 1 | **5** | 🟡 MEDIUM | SHA-256 digest at rest + `hmac.compare_digest` + plaintext printed exactly once (FR-03). Verified: FR-03 Gate 1 = 100.0; gitleaks 127 commits, no leaks; bug-hunt T-02/T-03 refuted. |
| R4 | 403 response discloses resource existence | security | 3 | 3 | 9 | 1 | **3** | 🟢 LOW | Authorisation decided *before* the resource lookup (FR-04 / §8 #6). Verified: FR-04 Gate 1 = 100.0; identical 401 detail string across missing/unknown/revoked paths (`api/deps.py:83`). |
| R5 | N+1 query collapses on large tables | performance | 4 | 5 | 20 | 3 | **15** | 🔴 **HIGH** | Explicit eager-load + SQL statement-count assertion (NFR-01 / §8 #14). **Partially verified only**: the statement-count guard passes, but the `pytest-benchmark` p95 measurement required by AC-N01.1/AC-N01.2 has never executed (`performance` dimension recorded `tool_score: null`, exit 5 "no tests ran"; `gate_score_overrides.performance = 75.0`). Likelihood cannot be reduced without the missing measurement. → see [MP-R5]. |
| R6 | Error body discloses internal structure | security | 4 | 3 | 12 | 1 | **3** | 🟢 LOW | RFC 7807 fixed field set + `detail` whitelist (FR-10). Verified: FR-10 Gate 1 = 100.0; Gate 4 `error_handling` = 100.0. |
| R7 | `CancelledError` swallowed → hang on shutdown | reliability | 3 | 3 | 9 | 1 | **3** | 🟢 LOW | Explicit prohibition + assertion tests (NFR-03). Verified: Gate 4 `error_handling` = 100.0 with zero issues; bug-hunt resilience lens produced 0 confirmed findings. |
| R8 | Task timeout leaves orphan subprocess | reliability | 3 | 3 | 9 | 2 | **6** | 🟡 MEDIUM | `kill()` followed by `await wait()` (FR-08 / §8 #25). Verified: FR-08 Gate 1 = 100.0. Likelihood not reduced to 1 because `taskq_api.service.runner` is a declared high-risk module and sits inside the mutation scope that still has 59 survivors (see R13). |
| R9 | Deploy proceeds without running the migration | operations | 3 | 5 | 15 | 3 | **15** | 🔴 **HIGH** | `/readyz` fails closed (FR-09 / §8 #11). **Evidence contradicted**: `test_readyz_happy_path_200` calls `api_health._ready()`, a name that does not exist — the source exposes `_is_database_ready()` (`api/health.py:51–63`). The readyz happy path therefore has no working test asserting fail-closed behaviour. → see [MP-R9]. |
| R10 | Connection pool exhaustion | reliability | 3 | 3 | 9 | 2 | **6** | 🟡 MEDIUM | `pool_pre_ping` + concurrency ceiling (FR-06/FR-08). Verified: FR-06 Gate 1 = 100.0. Likelihood not reduced to 1 — no load/soak test exists that drives the pool to its ceiling; correctness is proven, saturation behaviour is not. |
| R11 | Transitive dependency pulls incompatible licence | legal | 3 | 3 | 9 | 1 | **3** | 🟢 LOW | Lock file + full-tree scan (NFR-07). Verified: scancode 2134 files, 0 source licence detections, production deps all under the BSD/MIT/Apache/ISC/MPL/PSF allowlist; `license_compliance` = 100.0 at Gate 3 and Gate 4. |
| R12 | Rate-limit bucket race over-admits requests | correctness | 3 | 2 | 6 | 1 | **2** | 🟢 LOW | Single transaction + row-level lock (FR-05). Verified: FR-05 Gate 1 = 100.0; bug-hunt concurrency lens produced 0 confirmed findings. |

---

## 3. Register — Project-Discovered Risks (R13–R21)

Derived from Gate 3/4 dimension `issues[]`, `BASELINE.md::Known Issues`, and
`degradations.jsonl`. These are not in `SPEC.md` §9.

| ID | Name | Category | L | Imp. | **Score** | Band | Mitigation approach & verifying evidence |
|----|------|----------|---|------|-----------|------|------------------------------------------|
| R13 | 59 surviving mutants concentrated in high-risk modules | test-quality | 4 | 3 | **12** | 🔴 **HIGH** | Mutation score 77.6 against a 70.0 threshold — a **7.6-point margin**. Survivors cluster in `repository/session.py` and `repository/key_repo.py` (`mutation_survivors.json`), both declared high-risk modules. Any test deletion or refactor can drop the score below threshold and block a future gate. → see [MP-R13]. |
| R14 | Broken test masks the FR-09 readyz happy path | test-integrity | 5 | 3 | **15** | 🔴 **HIGH** | `test_readyz_happy_path_200` (`tests/test_extra_coverage.py`) calls a non-existent `api_health._ready()`. Already failing — likelihood 5 is *observed*, not forecast. Recorded LOW in `BASELINE.md:76`; escalated here because it is the test evidence for R9's fail-closed control. → see [MP-R14]. |
| R15 | 12 `pytest.mark.skip` markers vs the NFR-09 zero-skip rule | verification-integrity | 4 | 2 | **8** | 🟡 MEDIUM | NFR-09 states a zero-skip iron rule; 12 skips persist (incl. `test_migrations_env_fileconfig_branch`, deliberately not driven because it clobbers `caplog` in sibling tests). Line coverage is unaffected (100%). Documented in `BASELINE.md:79` as DEFERRED. |
| R16 | NFR-01 performance ACs never measured | verification-gap | 4 | 3 | **12** | 🔴 **HIGH** | `performance` dimension = `tool_score: null` ("pytest-benchmark: no tests ran (exit 5)"); Gate 4 shows 100.0 only because `gate_score_overrides.performance = 75.0` supplies a floor, not a measurement. AC-N01.1 (p95 < 30 ms) and AC-N01.2 (p95 < 80 ms @ 10k rows) are **unverified**. Root cause shared with R5. → see [MP-R16]. |
| R17 | `assert` used as a production invariant | robustness | 2 | 3 | **6** | 🟡 MEDIUM | bandit B101 LOW at `repository/session.py:136` (`assert _engine is not None`). Live in CI because tests run without `-O`; **stripped entirely under `python -O`**, turning a guarded invariant into an unguarded `None` dereference. Accepted at the gate (`security` = 99.0). |
| R18 | 17 zero-assertion meta-tests | test-quality | 3 | 2 | **6** | 🟡 MEDIUM | `test_assertion_quality` = 92.2. The 17 are NFR-guard meta-tests (e.g. `test_zero_skipped`, `test_no_deselect_or_k_filter`) whose assertion is indirect — pytest's own reported counts. The scorer cannot see the indirection; a real assertion regression in this set would also be invisible. |
| R19 | 12 CRG dead-code candidates | maintainability | 3 | 1 | **3** | 🟢 LOW | `QUALITY_REPORT.md::Dead Code Candidates`. Advisory only — the list includes FastAPI route handlers (`healthz`, `readyz`, `get_metrics`), exception handlers and pytest fixtures, i.e. framework-invoked entry points that are structurally invisible to the call graph. Expected false positives. **Do not delete.** |
| R20 | `task_repo.py` 79% coverage under integration-only runs | test-coverage | 3 | 2 | **6** | 🟡 MEDIUM | Uncovered under integration-only: lines 65–69 (ConflictError), 104/106/108 (status-filter branches), 166–171 (get_results ordering). Full suite reaches 100%. `integration_coverage` = 80.0 vs a 75.0 threshold — **5-point margin**. |
| R21 | Agent orchestration budget exhaustion | delivery-process | 4 | 2 | **8** | 🟡 MEDIUM | `degradations.jsonl` records 10 events: 7 `TURN_BUDGET`, 1 `TIMEOUT` (600 s wall clock), 1 `INFRA_ERROR`, 1 `EXECUTION_ERROR`, across FR-01…FR-09. Framework auto-escalated (40→80 turns, 600→1200 s) and every FR ultimately reached Gate 1 = 100.0. Impact is schedule, not product correctness. |

---

## 4. Distribution

| Band | Count | IDs |
|------|-------|-----|
| 🔴 HIGH (≥ 9) | 5 | R5, R9, R13, R14, R16 |
| 🟡 MEDIUM (4–8) | 9 | R1, R2, R3, R8, R10, R15, R17, R18, R20, R21 |
| 🟢 LOW (≤ 3) | 6 | R4, R6, R7, R11, R12, R19 |
| **Total** | **21** | R1–R21 |

By category: security 4 · reliability 3 · test-quality 3 · verification-gap 2 ·
data-integrity 1 · performance 1 · operations 1 · correctness 1 · legal 1 ·
robustness 1 · maintainability 1 · test-coverage 1 · delivery-process 1 ·
verification-integrity 1 · test-integrity 1.

**All five HIGH risks are verification gaps, not known defects.** Each is a control whose
evidence is missing, contradicted, or thin — none is a reproduced failure. This is consistent
with `06-quality/QUALITY_REPORT.md` (0 defects at every severity) and with the adversarial bug
hunt (12 raw findings, **0 confirmed**, 12 refuted with live repro).

---

## 5. What Would Falsify This Register

Stated explicitly so the assessment can be attacked rather than trusted:

1. **Residual likelihood is a judgement, not a measurement.** R1/R2/R3 are dropped to
   likelihood 1 on the strength of green gates. If the test suite systematically fails to
   exercise the dangerous path — the exact failure mode proven by R14 — then a green gate is
   evidence about the tests, not about the code. R14 is direct proof this happens in this repo,
   which is why R1–R3 sit at MEDIUM rather than LOW.
2. **Impact ratings are inherited from `SPEC.md` §9** and were never independently re-derived
   against the shipped architecture.
3. **R21 may be out of scope.** It is a delivery-process risk about the harness, not the
   product. Retained because it materially affected FR-01…FR-09 execution; remove it if the
   register is defined as product-only.
4. **The missing `deferred_fixes.md` / `issue_registry.json` may hold risks not represented
   here.** Their absence is recorded, not worked around. If they exist elsewhere, this register
   is incomplete by exactly their contents.

**Confidence: Medium-High.** High on the risk *inventory* (every entry traced to a named
artifact). Medium on the *residual likelihood* numbers (judgement, per point 1).

---

## 6. Cross-References

- Formal plans for the five HIGH risks: `07-risk/RISK_MITIGATION_PLANS.md`
- Current status, owners and target dates for all 21: `07-risk/RISK_STATUS_REPORT.md`
- Source matrix: `SPEC.md` §9
