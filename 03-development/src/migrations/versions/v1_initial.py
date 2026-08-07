"""v1 — initial schema (tasks, api_keys, rate_buckets).

[FR-07] First revision. Creates the three base tables: ``tasks`` (FR-01),
``api_keys`` (FR-03) and ``rate_buckets`` (FR-05). The ``tasks`` row carries
a ``result_json`` column from v1; v3 splits that payload out into a
dedicated ``task_results`` row. ``name`` is intentionally **not** unique in
v1 — v2 adds the unique index that gives FR-01 its 409-on-duplicate-name
behaviour.

Downgrade drops all three tables, leaving a clean ``alembic_version`` row
only (AC-7.2 — no residual tables).

Citations:
- SPEC.md#L79-L91 (FR-01 — tasks schema)
- SPEC.md#L101-L107 (FR-03 — api_keys schema)
- SPEC.md#L115-L120 (FR-05 — rate_buckets)
- SAD.md#L142 (§2.4 `migrations/versions/` directory)
- TEST_SPEC.md §FR-07 case 1 (upgrade head succeeds) / case 2 (downgrade base)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Column-length constants. Mirroring the SPEC.md §3 schemas explicitly so a
# change here is a single-site edit instead of a hunt through each column.
_ID_LEN = 36       # UUIDv4 string length
_NAME_LEN = 255    # task.name / api_key.scope
_STATUS_LEN = 32   # task.status enum string
_SCOPE_LEN = 32    # api_key.scope (mirrors task.status length)
_HASH_LEN = 64     # sha256 hex digest of an api key

# Alembic reads these module-level identifiers to build the revision graph.
revision = "v1_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply the v1 schema (AC-7.1).

    No destructive DROP-TABLE shortcut is used in this revision — FR-07
    AC-7.4 (enforced by ``tests/test_fr07.py::test_no_drop_table_shortcut``)
    forbids a raw ``op.execute`` containing ``DROP TABLE`` as a substitute
    for a real ``downgrade``.
    """
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=_ID_LEN), primary_key=True),
        sa.Column("name", sa.String(length=_NAME_LEN), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=_STATUS_LEN), nullable=False, server_default="pending"),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(length=_ID_LEN), primary_key=True),
        sa.Column("scope", sa.String(length=_SCOPE_LEN), nullable=False),
        sa.Column("key_hash", sa.String(length=_HASH_LEN), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "rate_buckets",
        sa.Column("key_id", sa.String(length=_ID_LEN), primary_key=True),
        sa.Column("tokens", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Reverse v1 — drop every v1 table (AC-7.2 leaves zero residual tables)."""
    op.drop_table("rate_buckets")
    op.drop_table("api_keys")
    op.drop_table("tasks")
