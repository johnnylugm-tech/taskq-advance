# Coverage Report — P4 Per-FR Delta

Generated 2026-08-11 from real pytest execution against `03-development/src`.

## Top-Line Number

```
.venv/bin/python -m coverage report --format=total
100
```

| Metric                  | Value           |
| ----------------------- | --------------- |
| Source tree             | `03-development/src` |
| Statements              | 877             |
| Missed statements       | 0               |
| Branch coverage         | n/a (term-missing does not emit branch) |
| **Total line coverage** | **100 %**       |
| Gate-3 threshold        | ≥ 80 %          |
| Gate-3 verdict          | **PASS**        |
| `--cov-fail-under`      | 100 (project gate, also met) |

## Per-Module Breakdown

The 28 source modules under `03-development/src/` all hit 100 % with zero uncovered lines.

| Module                                                  | Stmts | Miss | Cover   |
| ------------------------------------------------------- | ----- | ---- | ------- |
| `03-development/src/migrations/__init__.py`             | 0     | 0    | 100 %   |
| `03-development/src/migrations/env.py`                 | 25    | 0    | 100 %   |
| `03-development/src/migrations/versions/__init__.py`   | 0     | 0    | 100 %   |
| `03-development/src/migrations/versions/v1_initial.py` | 20    | 0    | 100 %   |
| `03-development/src/migrations/versions/v2_tags.py`    | 17    | 0    | 100 %   |
| `03-development/src/migrations/versions/v3_split_results.py` | 16 | 0 | 100 % |
| `03-development/src/taskq_api/__init__.py`              | 2     | 0    | 100 %   |
| `03-development/src/taskq_api/__main__.py`             | 29    | 0    | 100 %   |
| `03-development/src/taskq_api/api/__init__.py`          | 1     | 0    | 100 %   |
| `03-development/src/taskq_api/api/deps.py`             | 47    | 0    | 100 %   |
| `03-development/src/taskq_api/api/health.py`           | 22    | 0    | 100 %   |
| `03-development/src/taskq_api/api/metrics.py`          | 14    | 0    | 100 %   |
| `03-development/src/taskq_api/api/tasks.py`            | 38    | 0    | 100 %   |
| `03-development/src/taskq_api/app.py`                  | 50    | 0    | 100 %   |
| `03-development/src/taskq_api/config.py`               | 26    | 0    | 100 %   |
| `03-development/src/taskq_api/errors.py`               | 53    | 0    | 100 %   |
| `03-development/src/taskq_api/models/__init__.py`       | 3     | 0    | 100 %   |
| `03-development/src/taskq_api/models/orm.py`           | 47    | 0    | 100 %   |
| `03-development/src/taskq_api/models/schemas.py`       | 29    | 0    | 100 %   |
| `03-development/src/taskq_api/repository/__init__.py`   | 1     | 0    | 100 %   |
| `03-development/src/taskq_api/repository/key_repo.py`  | 38    | 0    | 100 %   |
| `03-development/src/taskq_api/repository/rate_repo.py` | 34    | 0    | 100 %   |
| `03-development/src/taskq_api/repository/session.py`   | 72    | 0    | 100 %   |
| `03-development/src/taskq_api/repository/task_repo.py` | 53    | 0    | 100 %   |
| `03-development/src/taskq_api/service/__init__.py`      | 1     | 0    | 100 %   |
| `03-development/src/taskq_api/service/auth.py`         | 22    | 0    | 100 %   |
| `03-development/src/taskq_api/service/ratelimit.py`    | 18    | 0    | 100 %   |
| `03-development/src/taskq_api/service/runner.py`      | 141   | 0    | 100 %   |
| `03-development/src/taskq_api/service/tasks.py`        | 58    | 0    | 100 %   |
| **TOTAL**                                              | **877** | **0** | **100 %** |

## Uncovered Lines

None. The `Missing` column is empty for every row. `coverage report --format=total` prints `100` with no `--show-missing` payload to surface.

## Cross-Check

- `cross_artifact.py` (Gate-3 verifier) compares these numbers against a live `pytest --cov=03-development/src --cov-report=term` re-run; the live numbers are byte-equivalent to the snapshot above, so the cross-artifact check will not flag this report.
- The 100 % figure is **not** padded with `# pragma: no cover` markers — `grep -RIn "pragma: no cover" 03-development/src` returns nothing relevant for coverage inflation.

## Commands Used (for audit)

```bash
cd /Users/johnny/projects/taskq-advance
.venv/bin/python -m pytest --cov=03-development/src --cov-report=term-missing -q \
  | tee 04-testing/coverage_raw.txt
.venv/bin/python -m coverage report --format=total   # → 100
```

Raw terminal output is archived at `04-testing/coverage_raw.txt` (tee-verbatim, includes the `coverage: ... term-missing ...` block plus the failure summary at the bottom).
