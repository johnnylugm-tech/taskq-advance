# RISK_STATUS_REPORT.md — taskq-advance

> **Phase**: 7 — Risk Management
> **Report date**: 2026-08-11
> **Register**: `07-risk/RISK_REGISTER.md` (21 risks)
> **Plans**: `07-risk/RISK_MITIGATION_PLANS.md` (5 formal plans)

---

## 1. Executive Status

**Overall posture: PASS with five open verification gaps.**

Quality evidence is uniformly green — Gate 1 100.0 across 10/10 FRs, Gate 2 93.14,
Gate 3 94.79, Gate 4 95.98, 100% line coverage, 0 defects at every severity in
`06-quality/QUALITY_REPORT.md`, 0 confirmed findings from a 12-finding adversarial bug hunt.

The five HIGH risks are **not defects**. Every one is a control whose evidence is missing,
contradicted or thin. The distinction matters for the release decision: nothing is known to be
broken, but five things are not known to work.

| Metric | Value | Source |
|---|---|---|
| Total risks tracked | 21 | `RISK_REGISTER.md` |
| HIGH (≥ 9) | 5 | R5, R9, R13, R14, R16 |
| MEDIUM (4–8) | 9 | R1, R2, R3, R8, R10, R15, R17, R18, R20, R21 |
| LOW (≤ 3) | 6 | R4, R6, R7, R11, R12, R19 |
| Formal mitigation plans | 5 | `RISK_MITIGATION_PLANS.md` |
| Closed / accepted | 0 | — |
| Confirmed defects | **0** | `bug_hunt_report.json`, `QUALITY_REPORT.md` |

**Root-cause concentration**: three of the five HIGH risks reduce to two causes — one broken
test (R14 → R9) and one missing benchmark fixture (R16 → R5). Fixing two things retires three
HIGH risks.

---

## 2. HIGH Risk Status — Owners and Target Dates

All five are **Open**. No mitigation has been executed; this phase produced plans, not fixes.

| ID | Risk | Score | Status | Owner | Target date | Plan | Blocked by |
|----|------|-------|--------|-------|-------------|------|------------|
| R14 | Broken test masks FR-09 readyz happy path | 15 | 🔴 Open | QA · ARCHITECT | 2026-08-12 (A1, P7 exit) | [MP-R14] | — |
| R9 | Deploy proceeds without running the migration | 15 | 🔴 Open | DEVOPS · QA | 2026-08-14 (A2, P8 exit) | [MP-R9] | R14 |
| R5 | N+1 query collapse on large tables | 15 | 🔴 Open | QA · ARCHITECT | 2026-08-14 (A2, P8 exit) | [MP-R5] | R16 |
| R16 | NFR-01 performance ACs never measured | 12 | 🔴 Open | QA · ARCHITECT | 2026-08-14 (A2, P8 exit) | [MP-R16] | — |
| R13 | 59 surviving mutants in high-risk modules | 12 | 🔴 Open | QA | 2026-08-18 (A3, maintenance) | [MP-R13] | — |

> **Dates are proposed, not agreed.** Anchored to FSM transitions (`advance-phase --completed 7`
> / `--completed 8` / first maintenance cycle) because no delivery calendar exists in any
> project artifact. **Requires Johnny's confirmation.** Owners are harness role tokens
> (`SAD.md:3179`, `phase_auditor.py:216–218`); real individuals are **Unknown** and were not
> invented.

### Movement since the SPEC was authored

| ID | Inherent (SPEC §9) | Residual now | Δ | Driver |
|----|--------------------|--------------|---|--------|
| R5 | 20 | 15 | ▼ 5 | Statement-count guard verified; timing still unmeasured |
| R9 | 15 | 15 | — | Control implemented, but its test is broken (R14) |
| R1 | 15 | 5 | ▼ 10 | FR-07 round-trip reversibility test, Gate 1 = 100.0 |
| R3 | 15 | 5 | ▼ 10 | Hashed at rest, constant-time compare, gitleaks clean |
| R6 | 12 | 3 | ▼ 9 | RFC 7807 fixed fields; `error_handling` = 100.0 |
| R2 | 10 | 5 | ▼ 5 | bandit HIGH=0; import-linter contract KEPT |

Nine SPEC risks reduced; **R9 did not move**, and R13/R14/R16 are new discoveries that did not
exist in the original matrix.

---

## 3. MEDIUM Risk Status — Monitoring Only

No formal plan required (residual < 9). Each has a named trigger that would escalate it.

| ID | Risk | Score | Status | Owner | Review point | Escalation trigger |
|----|------|-------|--------|-------|--------------|--------------------|
| R8 | Orphan subprocess after task timeout | 6 | 🟡 Monitored | QA | With MP-R13 | A `runner.py` mutant survives that touches `kill()`/`wait()` |
| R10 | Connection pool exhaustion | 6 | 🟡 Monitored | DEVOPS | P8 release checklist | Any production pool-timeout observed |
| R15 | 12 skips vs NFR-09 zero-skip rule | 8 | 🟡 Accepted-with-note | QA | P9 | Skip count rises above 12 |
| R17 | `assert` as production invariant (`session.py:136`) | 6 | 🟡 Monitored | ARCHITECT | P8 config review | Deployment runs Python with `-O` — control vanishes silently |
| R18 | 17 zero-assertion meta-tests | 6 | 🟡 Monitored | QA | P9 | `test_assertion_quality` drops below 85 |
| R20 | `task_repo.py` 79% under integration-only runs | 6 | 🟡 Monitored | QA | P9 | `integration_coverage` drops below 78 (5-pt margin) |
| R21 | Agent orchestration budget exhaustion | 8 | 🟡 Monitored | Johnny | Each phase | A step fails after budget auto-escalation |
| R1 | v3 migration data loss | 5 | 🟡 Residual-accepted | ARCHITECT | P8 release | Any schema change lands without a round-trip test |
| R2 | SQL injection | 5 | 🟡 Residual-accepted | ARCHITECT | Continuous (CI) | bandit reports any HIGH/MEDIUM |
| R3 | API key leak | 5 | 🟡 Residual-accepted | DEVOPS | Continuous (CI) | gitleaks reports a non-fixture finding |

> **R17 deserves a second look at P8.** It is scored MEDIUM on the assumption that production
> runs Python without `-O`. That assumption is **unverified**. If any deployment path enables
> `-O`, `assert _engine is not None` is removed by the interpreter and the invariant becomes an
> unguarded `None` dereference — at which point R17 is HIGH, not MEDIUM. The P8 configuration
> review is the correct place to settle it.

---

## 4. LOW Risk Status — Closed to Monitoring

| ID | Risk | Score | Status | Owner | Basis |
|----|------|-------|--------|-------|-------|
| R4 | 403 discloses resource existence | 3 | 🟢 Controlled | ARCHITECT | Authz precedes lookup; identical 401 detail across all paths |
| R6 | Error body discloses internals | 3 | 🟢 Controlled | ARCHITECT | RFC 7807 fixed fields; `error_handling` = 100.0 |
| R7 | `CancelledError` swallowed | 3 | 🟢 Controlled | QA | NFR-03 prohibition + assertion tests; resilience lens clean |
| R11 | Incompatible transitive licence | 3 | 🟢 Controlled | DEVOPS | scancode 2134 files, 0 detections; `license_compliance` = 100.0 |
| R12 | Rate-bucket race over-admits | 2 | 🟢 Controlled | QA | Single txn + row lock; FR-05 Gate 1 = 100.0 |
| R19 | 12 CRG dead-code candidates | 3 | 🟢 Advisory — **do not action** | ARCHITECT | Route handlers, exception handlers and fixtures — framework-invoked, structurally invisible to the call graph. Expected false positives. |

---

## 5. Gate Reconciliation

| Gate | Score | Verdict | Risk-relevant notes |
|------|-------|---------|---------------------|
| Gate 1 (per-FR, P7) | 100.0 × 10 FRs | ✅ PASS | `fr_progress.json` — all 10 at `gate1_pass` |
| Gate 2 (P3 exit) | 93.14 | ✅ PASS | mutation 77.8 (framework override), architecture 91.7 |
| Gate 3 (P4 exit) | 94.79 | ✅ PASS | `performance` recorded `tool_score: null` → **R16** |
| Gate 4 (P6 full) | 95.98 | ✅ PASS | 4 dimensions carry `issues[]` → **R13, R17, R18, R20** |

Dimensions running on a threshold floor rather than a measurement — the mechanism behind R16:

| Dimension | Gate 4 score | Override floor | Real measurement? |
|---|---|---|---|
| `performance` | 100.0 | 75.0 | ❌ **No** — "pytest-benchmark: no tests ran (exit 5)" |
| `mutation_testing` | 77.6 | 70.0 | ✅ Yes — 204 killed / 59 survived |
| `integration_coverage` | 80.0 | 75.0 | ✅ Yes — 39 integration tests |
| `test_assertion_quality` | 92.2 | 70.0 | ✅ Yes |

**`performance` is the only dimension whose green score reflects no measurement at all.**

---

## 6. Release Recommendation

**Conditional PASS for P7 → P8.** The phase deliverables exist and all FRs hold Gate 1 = 100.0.

Conditions, in priority order:

1. **Before P7 exit (A1)** — fix R14. It is a one-line call-site correction plus a real
   assertion, and it unblocks R9. Cheapest HIGH risk in the register.
2. **Before P8 exit / release (A2)** — close R16 and R5. Shipping with two numeric NFR
   acceptance criteria never measured is a decision that should be made explicitly by Johnny,
   not absorbed by a threshold override.
3. **First maintenance cycle (A3)** — close R13.

**Not recommended**: advancing past P8 with R16 open. Every other risk has either a working
control or a working test. R16 has neither, and the Gate 4 dashboard displays it as 100.0,
which is the most misleading signal in the entire evidence set.

---

## 7. Self-Review

**Two ways this report is most likely wrong:**

1. **The HIGH set may be over-stated.** R5, R9, R13, R14 and R16 are all "evidence missing",
   not "behaviour broken". If the controls do work — and the uniformly green gates plus a
   0-confirmed bug hunt are real evidence that they mostly do — then this report inflates
   process risk into product risk and delays a release that is genuinely ready.
2. **The HIGH set may be under-stated.** R14 proves this codebase contains at least one test
   that asserts nothing while appearing to pass coverage. The register found it by reading
   `BASELINE.md`, not by systematically auditing the suite. **No such audit was performed.**
   If R14 is not unique, the residual likelihoods for R1/R2/R3 — all lowered on the strength of
   green tests — are too optimistic. This is the single largest weakness in the assessment.

**Unverified assumptions (complete list):**
- Proposed A1/A2/A3 dates match real phase timing. **Requires Johnny's confirmation.**
- `pytest-benchmark` is installed and runnable (exit 5 indicates empty collection, not a
  missing plugin — but this was **not** verified).
- Production Python does not run with `-O` (drives R17's MEDIUM rating).
- Role tokens QA / ARCHITECT / DEVOPS map to accountable parties. **Unknown.**
- `.methodology/deferred_fixes.md` and `.sessi-work/issue_registry.json` do not exist anywhere
  in the project. Verified absent at the paths named by the P7 workflow; **not** searched
  exhaustively across the filesystem.

**Confidence: Medium-High.** High on the inventory and on gate reconciliation — every figure is
traced to a named artifact and none was estimated. Medium on residual likelihoods and on
dates, per the two failure modes above.

---

## 8. References

- `07-risk/RISK_REGISTER.md` — full 21-risk register with evidence provenance
- `07-risk/RISK_MITIGATION_PLANS.md` — MP-R5, MP-R9, MP-R13, MP-R14, MP-R16
- `SPEC.md` §9 — original 12-risk matrix
- `06-quality/QUALITY_REPORT.md`, `05-verification/BASELINE.md`, `05-verification/VERIFICATION_REPORT.md`
- `.methodology/gate{1,2,3,4}_result.json`, `mutation_survivors.json`, `bug_hunt_report.json`, `degradations.jsonl`
