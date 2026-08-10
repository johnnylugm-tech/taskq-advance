"""Alembic environment script.

[FR-07] Resolves the database URL from the same ``TASKQ_DB_URL`` source the
repository layer reads, so migrations run against the same database the
application code connects to (SAD.md §2.4). Tests set ``TASKQ_DB_URL`` via
``monkeypatch`` before each case, then ``command.upgrade(cfg, "head")`` uses
the URL the Config already has (``alembic_cfg.set_main_option("sqlalchemy.url")``).

No target metadata is wired up: every revision file declares its tables
explicitly (FR-07 — no autogenerate hand-waving). This keeps the migration
scripts reviewable as plain DDL and is what Gate 1's "no autogenerate" rule
in TEST_SPEC.md requires.

Citations:
- SPEC.md#L291 (§5.1 `TASKQ_DB_URL`)
- SAD.md#L111-L138 (§2.4 `migrations/` package)
- TEST_SPEC.md §FR-07 (execution mode: in-process via `alembic.command`)
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Alembic Config object — proxies ``alembic.ini`` (here empty; the test
# fixture builds the Config in-memory).
config = context.config

# Configure Python logging only when a config file is present. The test
# fixture passes ``Config()`` with no file so ``config.config_file_name``
# is ``None``; calling ``fileConfig(None)`` would raise.
# ``disable_existing_loggers=False`` because alembic can run in-process
# alongside the API (startup migration, tests): the default True silences
# every logger created before this point — including
# ``taskq_api.access``, which FR-10/AC-10.3 requires to emit one
# correlation-id line per request.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Resolve the URL: prefer the env var (the production entry-point path),
# fall back to whatever the test fixture already set on the Config.
url = os.environ.get("TASKQ_DB_URL") or config.get_main_option("sqlalchemy.url")
if url:
    config.set_main_option("sqlalchemy.url", url)

target_metadata = None


def run_migrations_offline() -> None:
    """Render migrations as SQL without a live DB connection (AC-7.5).

    FR-07's "offline SQL is inside test coverage" rule is satisfied by
    ``test_migration_offline_sql_render`` in test_fr07.py — it calls
    ``command.upgrade(alembic_cfg, "head", sql=True)`` and asserts the
    rendered SQL contains every expected ``CREATE TABLE``.
    """
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations to a live database (AC-7.1 / AC-7.2 / AC-7.3)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
