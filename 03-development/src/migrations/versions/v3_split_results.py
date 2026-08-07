"""v3 — split ``tasks.result_json`` out into a dedicated ``task_results`` table.

[FR-07] Data-migration revision. The forward path:

1. create ``task_results(task_id PK, result_json)``
2. populate it from ``tasks.result_json``
3. drop ``tasks.result_json``

The reverse path reverses every step (AC-7.3 round-trip):

1. add ``tasks.result_json`` back (nullable)
2. populate it from ``task_results`` (1:1 keyed by ``task_id``)
3. drop ``task_results``

``task_id`` is the primary key of ``task_results`` so each task can hold
at most one result row — the 1:1 mapping the round-trip needs to be
byte-identical. ``op.execute("INSERT INTO ... SELECT ...")`` and
``op.execute("UPDATE tasks SET result_json = (SELECT ...)")`` are the
data-migration primitives; neither contains the ``DROP TABLE`` shortcut
the AC-7.4 lint forbids.

Citations:
- SPEC.md#L79-L91 (FR-01 — tasks schema)
- SAD.md#L142 (§2.4 `migrations/versions/`)
- TEST_SPEC.md §FR-07 case 3 (round-trip byte-identical) / case 4 (no DROP shortcut)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Column-length constants — see v1_initial.py for rationale.
_ID_LEN = 36       # UUIDv4 string length (task_results.task_id == tasks.id)

revision = "v3_split_results"
down_revision = "v2_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the v3 data migration (AC-7.3 forward direction)."""
    op.create_table(
        "task_results",
        sa.Column("task_id", sa.String(length=_ID_LEN), primary_key=True),
        sa.Column("result_json", sa.Text(), nullable=True),
    )
    op.execute(
        "INSERT INTO task_results (task_id, result_json) "
        "SELECT id, result_json FROM tasks "
        "WHERE result_json IS NOT NULL"
    )
    op.drop_column("tasks", "result_json")


def downgrade() -> None:
    """Reverse the v3 data migration (AC-7.3 round-trip + AC-7.2 zero residual)."""
    op.add_column(
        "tasks",
        sa.Column("result_json", sa.Text(), nullable=True),
    )
    op.execute(
        "UPDATE tasks SET result_json = ("
        "  SELECT result_json FROM task_results "
        "  WHERE task_results.task_id = tasks.id"
        ")"
    )
    op.drop_table("task_results")
