"""FR-07 — Schema Migration (Alembic three-step evolution with data migration).

[FR-07] Test cases (1..5) from TEST_SPEC.md §"FR-07: Schema Migration
(Alembic three-step evolution with data migration)". Function names match the
TEST_SPEC catalog verbatim so spec-coverage-check can match them exactly.

Implementation contract (SAB-declared module names, Gate 1 binding):
  * ``migrations.versions.v1_initial``  -> src/migrations/versions/v1_initial.py
  * ``migrations.versions.v2_tags``     -> src/migrations/versions/v2_tags.py
  * ``migrations.versions.v3_split_results``
                                        -> src/migrations/versions/v3_split_results.py
  * ``taskq_api.repository.session``    -> src/taskq_api/repository/session.py

Every import below is a plain top-level import. Until the FR-07 migration
package lands (and until ``alembic`` is installed as a pinned dependency),
pytest exits with a Collection Error (``ModuleNotFoundError``) — that is the
intended TDD RED state and must NOT be papered over with try/except ImportError.

Execution mode: **in-process**. Alembic is driven through its Python API
(``alembic.config.Config`` + ``alembic.command``) rather than a subprocess, so
pytest-cov can measure the migration modules themselves — TEST_SPEC case 5 and
FR-07's rule "migration files themselves are inside test coverage" require it.
The real SQLite *file* under ``tmp_path`` (not ``:memory:``) is still used for
cases 1, 2 and 3, satisfying NFR-09.5's real-DB requirement.

Citations:
- SPEC.md §3 FR-07 (three revisions, real downgrade, round-trip, no DROP shortcut)
- SAD.md §2.4 `migrations/versions/`, §3 schema-evolution flow
- TEST_SPEC.md §FR-07 cases 1-5 and sub-assertion table
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, create_engine, inspect, text

# SAB-declared modules for FR-07. Imported at module scope on purpose: the
# ModuleNotFoundError they raise before GREEN *is* the RED signal, and it also
# pins the implementation to the SAB names so Gate 1 sees no phantom module.
from migrations.versions import v1_initial, v2_tags, v3_split_results
from taskq_api.repository import session as repo_session

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
VERSIONS_DIR = SRC_ROOT / "migrations" / "versions"

# All six tables expected at head: v1 -> tasks, api_keys, rate_buckets;
# v2 -> tags, task_tags; v3 -> task_results (SAD.md §2.4).
EXPECTED_HEAD_TABLES = {
    "tasks",
    "api_keys",
    "rate_buckets",
    "tags",
    "task_tags",
    "task_results",
}
EXPECTED_HEAD_TABLE_COUNT = 6  # FR07-upgrade-head-table-count / FR07-offline-sql-non-empty


@pytest.fixture()
def sqlite_file(tmp_path):
    """A fresh, empty SQLite *file* per test (TEST_SPEC db_kind=sqlite_file).

    Function-scoped so no migration state leaks between cases.
    """
    db_path = tmp_path / "fr07-migration.db"
    return db_path


@pytest.fixture()
def alembic_cfg(sqlite_file, monkeypatch):
    """Alembic ``Config`` bound to the per-test SQLite file.

    ``TASKQ_DB_URL`` is also set so ``migrations/env.py`` may resolve the URL
    through ``taskq_api.repository.session`` / ``taskq_api.config`` exactly as
    it does in production (SAD.md §2.4 dependency on the repository layer).
    """
    url = f"sqlite:///{sqlite_file}"
    monkeypatch.setenv("TASKQ_DB_URL", url)
    repo_session.reset_engine()

    cfg = Config()
    cfg.set_main_option("script_location", str(SRC_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def _table_names(sqlite_file) -> set[str]:
    """Reflect the user tables present in the SQLite file, ignoring internals."""
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    return {n for n in names if not n.startswith("sqlite_")}


def _row_dump(sqlite_file, table: str) -> list[tuple]:
    """Return every row of ``table`` as sorted tuples for byte-identical compare."""
    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        meta = MetaData()
        meta.reflect(bind=engine, only=[table])
        tbl = meta.tables[table]
        cols = sorted(c.name for c in tbl.columns)
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT {', '.join(cols)} FROM {table}")  # noqa: S608 - fixed identifiers
            ).fetchall()
    finally:
        engine.dispose()
    return sorted(tuple(r) for r in rows)


# ---------------------------------------------------------------------------
# Case 1 — AC-7.1
# ---------------------------------------------------------------------------
def test_upgrade_head_succeeds(alembic_cfg, sqlite_file):
    """[FR-07] AC-7.1 — `alembic upgrade head` succeeds on a fresh empty DB.

    rule_id: FR07-upgrade-head-exit-zero (exit_code_val == "0")
    rule_id: FR07-upgrade-head-table-count (table_count_v3 == "6")
    """
    assert not sqlite_file.exists(), "precondition: fresh empty SQLite file"

    # In-process invocation; a raised exception is the in-process equivalent of
    # a non-zero exit code, so completing without raising == exit_code_val "0".
    command.upgrade(alembic_cfg, "head")

    tables = _table_names(sqlite_file)
    assert EXPECTED_HEAD_TABLES <= tables, f"missing tables at head: {EXPECTED_HEAD_TABLES - tables}"
    assert len(tables - {"alembic_version"}) == EXPECTED_HEAD_TABLE_COUNT

    # v1 alone must have produced exactly the two FR-07 v1 tables + rate_buckets.
    assert v1_initial.down_revision is None
    assert v2_tags.down_revision == v1_initial.revision
    assert v3_split_results.down_revision == v2_tags.revision


# ---------------------------------------------------------------------------
# Case 2 — AC-7.2
# ---------------------------------------------------------------------------
def test_downgrade_base_leaves_no_residual(alembic_cfg, sqlite_file):
    """[FR-07] AC-7.2 — `downgrade base` succeeds and leaves no residual tables.

    rule_id: FR07-downgrade-base-exit-zero (exit_code_val == "0")
    rule_id: FR07-downgrade-base-zero-residual (residual_table_count == "0")
    """
    command.upgrade(alembic_cfg, "head")
    assert EXPECTED_HEAD_TABLES <= _table_names(sqlite_file)

    command.downgrade(alembic_cfg, "base")

    residual = _table_names(sqlite_file) - {"alembic_version"}
    assert residual == set(), f"residual tables after downgrade base: {sorted(residual)}"
    assert len(residual) == 0


# ---------------------------------------------------------------------------
# Case 3 — AC-7.3
# ---------------------------------------------------------------------------
def test_round_trip_byte_identical(alembic_cfg, sqlite_file):
    """[FR-07] AC-7.3 — upgrade head → write 5 rows → downgrade -1 → upgrade head
    leaves every column byte-identical (v3 data migration).

    rule_id: FR07-round-trip-byte-identical
             (field_byte_identical == "true" and sample_row_count == "5")
    """
    sample_row_count = 5
    command.upgrade(alembic_cfg, "head")

    engine = create_engine(f"sqlite:///{sqlite_file}")
    try:
        with engine.begin() as conn:
            for i in range(sample_row_count):
                conn.execute(
                    text(
                        "INSERT INTO tasks (id, name, command, status) "
                        "VALUES (:id, :name, :command, :status)"
                    ),
                    {
                        "id": f"task-{i:04d}",
                        "name": f"round-trip-{i}",
                        "command": f"echo payload-{i}",
                        "status": "succeeded",
                    },
                )
                # Distinct result_json payload per row (TEST_SPEC precondition).
                conn.execute(
                    text(
                        "INSERT INTO task_results (task_id, result_json) "
                        "VALUES (:task_id, :result_json)"
                    ),
                    {
                        "task_id": f"task-{i:04d}",
                        "result_json": '{"stdout": "payload-%d", "exit_code": %d}' % (i, i),
                    },
                )
    finally:
        engine.dispose()

    before_tasks = _row_dump(sqlite_file, "tasks")
    before_results = _row_dump(sqlite_file, "task_results")
    assert len(before_results) == sample_row_count

    command.downgrade(alembic_cfg, "-1")
    # After reversing v3 the split table is gone and the column is back.
    assert "task_results" not in _table_names(sqlite_file)
    packed = _row_dump(sqlite_file, "tasks")
    assert len(packed) == sample_row_count, "v3 downgrade lost task rows"

    command.upgrade(alembic_cfg, "head")

    assert _row_dump(sqlite_file, "tasks") == before_tasks
    assert _row_dump(sqlite_file, "task_results") == before_results


# ---------------------------------------------------------------------------
# Case 4 — AC-7.4
# ---------------------------------------------------------------------------
def test_no_drop_table_shortcut():
    """[FR-07] AC-7.4 — no `op.execute("DROP TABLE ...")` shortcut in versions/.

    rule_id: FR07-no-drop-table-shortcut (drop_table_shortcut_present == "false")
    """
    assert VERSIONS_DIR.is_dir(), f"migration versions dir missing: {VERSIONS_DIR}"

    version_files = sorted(p for p in VERSIONS_DIR.glob("*.py") if p.name != "__init__.py")
    assert len(version_files) >= 3, "expected v1/v2/v3 revision files"

    shortcut = re.compile(r"op\.execute\(\s*[\"'f]*[^)]*\bDROP\s+TABLE\b", re.IGNORECASE)
    offenders = [p.name for p in version_files if shortcut.search(p.read_text(encoding="utf-8"))]
    assert offenders == [], f"destructive DROP TABLE shortcut found in: {offenders}"

    # Each revision must expose a real, non-empty downgrade().
    for module in (v1_initial, v2_tags, v3_split_results):
        assert callable(getattr(module, "upgrade", None))
        assert callable(getattr(module, "downgrade", None))


# ---------------------------------------------------------------------------
# Case 5 — AC-7.5
# ---------------------------------------------------------------------------
def test_migration_offline_sql_render(alembic_cfg, capsys):
    """[FR-07] AC-7.5 — `upgrade head --sql` renders non-empty offline SQL
    creating all six tables.

    rule_id: FR07-offline-sql-non-empty
             (non_empty_sql == "true" and table_count == "6")
    """
    buf = io.StringIO()
    alembic_cfg.stdout = buf

    command.upgrade(alembic_cfg, "head", sql=True)

    sql = buf.getvalue() or capsys.readouterr().out
    assert sql.strip(), "offline SQL generation produced no output"

    lowered = sql.lower()
    created = {name for name in EXPECTED_HEAD_TABLES if f"create table {name}" in lowered}
    assert created == EXPECTED_HEAD_TABLES, f"offline SQL missing: {EXPECTED_HEAD_TABLES - created}"
    assert len(created) == EXPECTED_HEAD_TABLE_COUNT
