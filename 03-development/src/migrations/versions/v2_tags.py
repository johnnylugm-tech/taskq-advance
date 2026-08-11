"""v2 — tags (many-to-many) and unique index on ``tasks.name``.

[FR-07] Adds the ``tags`` table plus the ``task_tags`` association table
that joins ``tags`` to ``tasks``. Also adds the unique index on
``tasks.name`` that the v1 schema deliberately deferred — the unique
constraint is the only mechanism preventing a TOCTOU race on
duplicate-name creates (FR-01 AC-1.4).

Downgrade reverses every change: the unique index, the link table and
``tags`` are dropped. v1 data is untouched.

Citations:
- SPEC.md#L79-L91 (FR-01 — tasks.name uniqueness, AC-1.4)
- SAD.md#L142 (§2.4 `migrations/versions/`)
- TEST_SPEC.md §FR-07 case 1 (table_count_v3 == "6" with tags + task_tags present)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Column-length constants — see v1_initial.py for rationale.
_ID_LEN = 36       # UUIDv4 string length
_NAME_LEN = 255    # tag.name

revision = "v2_tags"
down_revision = "v1_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the v2 schema."""
    op.create_table(
        "tags",
        sa.Column("id", sa.String(length=_ID_LEN), primary_key=True),
        sa.Column("name", sa.String(length=_NAME_LEN), nullable=False, unique=True),
    )
    op.create_table(
        "task_tags",
        sa.Column("task_id", sa.String(length=_ID_LEN), sa.ForeignKey("tasks.id"), primary_key=True),
        sa.Column("tag_id", sa.String(length=_ID_LEN), sa.ForeignKey("tags.id"), primary_key=True),
    )
    op.create_index("uq_tasks_name", "tasks", ["name"], unique=True)


def downgrade() -> None:
    """Reverse v2 — does not touch any v1 data (AC-7.2)."""
    op.drop_index("uq_tasks_name", table_name="tasks")
    op.drop_table("task_tags")
    op.drop_table("tags")


# pragma: no error-handling
