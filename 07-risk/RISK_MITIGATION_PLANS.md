# RISK_MITIGATION_PLANS.md — taskq-advance

> **Phase**: 7 — Risk Management
> **Generated**: 2026-08-11
> **Scope**: formal mitigation plan for every risk with residual score ≥ 9 (HIGH)
> **Source register**: `07-risk/RISK_REGISTER.md` §2–§3

---

## 0. Owner and Deadline Conventions

**Owners are role assignments, not individuals.** `harness/SAD.md:3179` assigns Phase 7
Agent A = DEVOPS and Agent B = ARCHITECT; `harness/scripts/phase_auditor.py:216–218` records
`agent_a: qa`, `agent_b: architect`. Real human names are **Unknown** — this document does not
invent them. Johnny is recorded as the accountable approver because he is the only named human
actor in the project instructions.

| Role token | Responsibility |
|---|---|
| **QA** (Agent A) | Authors tests and mitigation evidence |
| **ARCHITECT** (Agent B) | Reviews the mitigation, approves closure |
| **DEVOPS** | Runbook, deployment and CI-side controls |
| **Johnny** | Accountable approver; sole authority to accept a risk unmitigated |

**Deadlines are anchored to phase transitions, not to invented calendar commitments.**
Today is 2026-08-11 and the project is mid-Phase-7. Each date below is the *proposed*
completion point for the named FSM transition and **requires Johnny's confirmation** — the
harness does not publish a delivery calendar.

| Anchor | Proposed date | FSM event |
|---|---|---|
| **A1 — before P7 exit** | 2026-08-12 | `advance-phase --completed 7` |
| **A2 — before P8 exit** | 2026-08-14 | `advance-phase --completed 8` (release) |
| **A3 — P9 maintenance** | 2026-08-18 | first maintenance cycle |

> A mitigation that changes FR code must run full TDD per `phase7_plan.md`
> (`run-fr-step --step TDD-RED` → `TDD-GREEN` → `TDD-IMPROVE` → `GATE1`).
> **None of that is executed by this document** — this is a plan, not an implementation.

---

## [MP-R14] Broken test masks the FR-09 readyz happy path

| Field | Value |
|---|---|
| **Risk** | R14 — residual **15** (L5 × I3) 🔴 HIGH |
| **Owner** | **QA** (author) · **ARCHITECT** (review) |
| **Deadline** | **A1 — 2026-08-12, before P7 exit** |
| **Blocks** | [MP-R9] — R9's control cannot be verified while its test is broken |
| **Status** | Open |

### Problem
`test_readyz_happy_path_200` in `03-development/tests/test_extra_coverage.py` calls
`api_health._ready()`. That symbol does not exist. The source exposes `_is_database_ready()`
(`03-development/src/taskq_api/api/health.py:51–63`). The test has never asserted anything
about readyz behaviour; it fails at attribute lookup. Recorded LOW in `BASELINE.md:76` and
under `04-testing/TEST_RESULTS.md::Deferred / Failing Issues`.

### Why this is HIGH and not LOW
The baseline rates it LOW as a *test-code defect*. That rating measures the wrong thing.
This test is the evidence for R9's fail-closed control — the control that prevents shipping
against an unmigrated database. A broken test does not merely fail to add coverage; it
**creates a false impression that the path is covered**. Line coverage stays at 100% because
the call site is executed before it raises, so no coverage signal exposes the gap.

### Mitigation actions
1. Repoint the call site to `_is_database_ready()`; run and observe it **fail first** if the
   assertion is wrong, per the project TDD rule. — QA
2. Assert the *behaviour*, not the symbol: readyz returns 200 only when the migration head
   matches, and returns non-200 when it does not. — QA
3. Grep the whole test tree for other references to private source symbols that no longer
   exist; this class of drift is unlikely to be a single instance. — QA
4. ARCHITECT reviews whether any other `BASELINE.md` LOW item is similarly mis-rated because
   it was scored as a test defect rather than as missing evidence for a control.

### Success criteria (verifiable)
- `pytest -k readyz` → 0 failures, and the happy-path test asserts a status code.
- The grep in action 3 returns no further non-existent private-symbol references, or each hit
  gets its own register entry.
- Full suite still 100% coverage; Gate 1 for FR-09 re-runs at 100.0.

### Rollback / fallback
If the readyz contract turns out to be intentionally different from what the test asserts,
**delete the test rather than weaken the assertion**, and raise a new register entry for the
now-uncovered control. Do not make a test pass by asserting less than the control requires.

---

## [MP-R9] Deploy proceeds without running the migration

| Field | Value |
|---|---|
| **Risk** | R9 — residual **15** (L3 × I5) 🔴 HIGH |
| **Owner** | **DEVOPS** (runbook) · **QA** (test evidence) |
| **Deadline** | **A2 — 2026-08-14, before P8 exit** |
| **Blocked by** | [MP-R14] |
| **Status** | Open — control implemented, evidence missing |

### Problem
`SPEC.md` §9 R9 (impact 高): a deploy that forgets the migration is served traffic against a
schema the code does not expect. The mitigation — `/readyz` fails closed (FR-09 / §8 #11) — is
implemented and FR-09 holds Gate 1 = 100.0. But the one test that exercises the happy path is
broken (R14), so the *fail-closed* branch is asserted while the *pass* branch is not. A control
that always says "not ready" would score identically today.

### Mitigation actions
1. Land [MP-R14] first — no further work here is meaningful until readyz has a working test.
2. Add an integration test that boots against a deliberately **stale** schema and asserts
   `/readyz` returns non-200 with an RFC 7807 body. — QA
3. Add the complementary test: migrated-to-head schema → 200. Both branches, one commit. — QA
4. DEVOPS records in `08-config/RELEASE_CHECKLIST.md` (generated at P7→P8 advance) that
   `/readyz` must return 200 before traffic is admitted, and that a non-200 blocks rollout.
5. Confirm the readiness probe is actually wired to the orchestrator's gate, not merely
   exposed as an endpoint. If nothing consumes `/readyz`, the control is decorative. — DEVOPS

### Success criteria (verifiable)
- Two integration tests, stale-schema and head-schema, both asserting status codes.
- A `RELEASE_CHECKLIST.md` line item referencing `/readyz`.
- Named consumer of the probe identified in the deployment config, or the gap escalated.

### Residual after mitigation
Likelihood 3 → 1; residual score 15 → 5 (MEDIUM). Impact stays 5 — a missed migration is
severe regardless of how well it is detected.

---

## [MP-R16] NFR-01 performance acceptance criteria never measured

| Field | Value |
|---|---|
| **Risk** | R16 — residual **12** (L4 × I3) 🔴 HIGH |
| **Owner** | **QA** (benchmarks) · **ARCHITECT** (threshold sign-off) |
| **Deadline** | **A2 — 2026-08-14, before P8 exit** |
| **Paired with** | [MP-R5] — same root cause, same benchmark harness |
| **Status** | Open — dimension is a floor, not a measurement |

### Problem
AC-N01.1 (`GET /v1/tasks/{id}` p95 < 30 ms) and AC-N01.2 (`GET /v1/tasks?limit=50` p95 < 80 ms
at 10,000 rows) require `pytest-benchmark` measurements. `04-testing/TEST_RESULTS.md` contains
no benchmark row. The Gate 3 `performance` dimension recorded `tool_score: null` with evidence
`pytest-benchmark: no tests ran (exit 5) — dimension not yet applicable`. Gate 4 shows
`performance: 100.0` **solely because** `gate_score_overrides.performance = 75.0` supplies a
threshold floor. `05-verification/VERIFICATION_REPORT.md:150–167` acknowledges this and defers
it to `04-testing/TEST_PLAN.md::TP-NFR-08-B`.

### Why the deferral is worth re-examining
The P5 argument is that "no regression" is vacuously satisfied because there is no prior green
benchmark to regress against. That is internally consistent, but it means **an NFR with two
numeric acceptance criteria will reach release with neither number ever produced.** The floor
override makes the dimension *look* green on the Gate 4 dashboard, which is the failure mode
worth naming: the score communicates a measurement that does not exist.

### Mitigation actions
1. Add `pytest-benchmark` cases for both ACs with a 10,000-row seeded fixture. — QA
2. Record the measured p95 values in `04-testing/TEST_RESULTS.md` — the actual numbers, not a
   pass/fail flag. — QA
3. Re-run Gate 4's `performance` dimension so `tool_score` is a real number, then remove or
   re-justify `gate_score_overrides.performance`. — ARCHITECT
4. If the numbers miss the AC, **do not adjust the AC** — raise a new HIGH risk and escalate to
   Johnny. Moving the target to meet the measurement is the failure mode this plan exists to
   prevent.

### Success criteria (verifiable)
- `performance` dimension carries a non-null `tool_score` sourced from a benchmark run.
- Both p95 values recorded with their AC thresholds beside them.
- Override removed, or its retention justified in writing.

### Known limitation
Benchmarks on a developer machine are not a production latency claim. This plan verifies the
AC as written; it does not establish production performance. **Explicitly out of scope.**

---

## [MP-R5] N+1 query collapse on large tables

| Field | Value |
|---|---|
| **Risk** | R5 — residual **15** (L3 × I5) 🔴 HIGH |
| **Owner** | **QA** (evidence) · **ARCHITECT** (query review) |
| **Deadline** | **A2 — 2026-08-14, before P8 exit** |
| **Paired with** | [MP-R16] |
| **Status** | Open — structural guard passes, scale evidence absent |

### Problem
`SPEC.md` §9 R5 is the highest-rated inherent risk in the project (likelihood 高 × impact 高 =
20). The mitigation is explicit eager-loading plus a SQL statement-count assertion
(NFR-01 / §8 #14). The statement-count guard **does pass** — `VERIFICATION_REPORT.md:2.6`
confirms the list endpoint's statement count is constant with respect to row count, asserted
via a SQLAlchemy event listener in the integration suite.

The residual stays HIGH because a constant statement count is necessary but not sufficient.
One query over 10,000 unindexed rows is still one slow query, and the timing evidence that
would catch it is exactly the measurement missing under R16.

### Mitigation actions
1. Land [MP-R16] — the 10k-row benchmark fixture is the missing evidence for this risk too.
2. Assert the statement count at 10,000 rows, not only at fixture scale, so the guard is
   proven where the risk lives. — QA
3. ARCHITECT reviews the generated SQL for the list endpoint at 10k rows: confirm index usage
   and that pagination is applied in-database rather than in Python.
4. Record the resulting statement count and p95 together in `04-testing/TEST_RESULTS.md`, so a
   future regression in either dimension is visible.

### Success criteria (verifiable)
- Statement-count assertion runs at 10,000 rows and holds constant.
- Measured p95 for the list endpoint recorded and compared against AC-N01.2 (< 80 ms).
- Query plan reviewed and index usage confirmed in writing.

### Residual after mitigation
Likelihood 3 → 1; residual 15 → 5 (MEDIUM). If the benchmark misses AC-N01.2, likelihood rises
to 4 (residual 20) and this becomes the project's top risk — escalate to Johnny immediately
rather than deferring to P9.

---

## [MP-R13] 59 surviving mutants in high-risk modules

| Field | Value |
|---|---|
| **Risk** | R13 — residual **12** (L4 × I3) 🔴 HIGH |
| **Owner** | **QA** |
| **Deadline** | **A3 — 2026-08-18, first maintenance cycle** |
| **Status** | Open — above threshold, thin margin |

### Problem
Mutation score 77.6 (killed 204 / survived 59) against a 70.0 threshold — a **7.6-point
margin**. `mutation_survivors.json` shows survivors clustering in
`03-development/src/taskq_api/repository/session.py` and `repository/key_repo.py`. Both are
declared high-risk modules in `CLAUDE.md`. `session.py` is also where R17's stripped-under-`-O`
assert lives, and the module carrying the most survivors is the one whose failure mode is least
covered.

### Why the deadline is A3 and not A1/A2
This risk is **already above threshold** and blocks no gate today. Pulling it forward would
displace R14/R9/R16, which are genuine evidence gaps. Deliberate sequencing — recorded here so
the deferral is a decision rather than an omission.

### Mitigation actions
1. Extract the per-mutant detail for the `session.py` and `key_repo.py` survivors
   (`mutmut show <id>`) and classify each: genuinely-missed assertion vs. equivalent mutant. — QA
2. Write kill tests for the genuinely-missed group only. Equivalent mutants must be recorded
   with the reason they cannot be killed — **not** silently excluded via `setup.cfg`.
3. Re-run `mutation-test-score`; target a ≥ 15-point margin (score ≥ 85) so ordinary refactoring
   cannot push the project under threshold.
4. Any change to `setup.cfg` mutation exclusions must be reviewed by ARCHITECT — that file is
   score-altering and is hashed into `gate1_result.json::evidence_digest`.

### Success criteria (verifiable)
- Mutation score ≥ 85, or a written per-mutant justification for every remaining survivor in
  the two high-risk modules.
- No new entries in `setup.cfg` mutation exclusions without a recorded reason.

### Explicit non-goal
100% mutation kill is not the target. Chasing equivalent mutants produces tests that assert
implementation detail, which is the opposite of the project's test-behaviour-not-implementation
rule.

---

## Summary of Plans

| Plan | Risk | Score | Owner | Deadline | Depends on | Status |
|------|------|-------|-------|----------|------------|--------|
| [MP-R14] | R14 broken readyz test | 15 | QA · ARCHITECT | A1 · 2026-08-12 | — | Open |
| [MP-R9] | R9 unmigrated deploy | 15 | DEVOPS · QA | A2 · 2026-08-14 | MP-R14 | Open |
| [MP-R5] | R5 N+1 at scale | 15 | QA · ARCHITECT | A2 · 2026-08-14 | MP-R16 | Open |
| [MP-R16] | R16 NFR-01 unmeasured | 12 | QA · ARCHITECT | A2 · 2026-08-14 | — | Open |
| [MP-R13] | R13 mutation survivors | 12 | QA | A3 · 2026-08-18 | — | Open |

**Critical path**: MP-R14 → MP-R9. MP-R16 → MP-R5. MP-R13 is independent.
Two of the five HIGH risks are unblockable until a single broken test is fixed, and two more
share one missing benchmark fixture — **three of five HIGH risks collapse to two root causes.**

MEDIUM and LOW risks carry no formal plan by the ≥ 9 rule; their monitoring approach is in
`07-risk/RISK_STATUS_REPORT.md` §4.

---

## Self-Review

**Where this plan is most likely wrong:**

1. **The A1/A2/A3 dates are proposed, not agreed.** They are anchored to FSM transitions
   because no delivery calendar exists in any artifact. If P8 does not run on 2026-08-14, every
   A2 deadline is fiction. Johnny must confirm or replace them.
2. **MP-R13's A3 deferral could be wrong.** If a Phase 8 or 9 change touches
   `repository/session.py`, the 7.6-point margin could vanish and block a gate at the worst
   moment. The alternative — pull MP-R13 to A2 — was rejected to protect the evidence-gap work,
   but that trade is a judgement, not a derivation.

**Unverified assumptions:**
- That `pytest-benchmark` is installed and runnable in this environment. Not checked — Gate 3
  evidence shows only "exit 5 / no tests ran", which indicates collection found nothing, not
  that the plugin is absent. **Requires verification** before MP-R16 starts.
- That the readyz contract is what MP-R14 assumes (200 at head, non-200 when stale). Read from
  `SPEC.md` §9 R9 and FR-09, not from the implementation.
- That role tokens QA / ARCHITECT / DEVOPS map to actual accountable parties. **Unknown.**

**Confidence: Medium.** High that these five are the right HIGH risks and that the root-cause
collapse is real. Medium on deadlines (unconfirmed) and on owners (roles, not people).
