"""Operator CLI entry point — ``python -m taskq_api ...``.

[FR-03] AC-3.5: ``python -m taskq_api key create --scope <scope>`` mints a
new API key, prints the plaintext to stdout exactly once, and persists
only its SHA-256 hash in the ``api_keys`` table.

The CLI is intentionally a thin shim over :func:`taskq_api.service.auth.create_api_key`
— the service layer owns the policy ("hash before write, print once") and
the CLI's job is purely argument parsing + dispatch. New subcommands land
here in the same pattern.

The CLI bootstraps the schema (``Base.metadata.create_all``) on entry so an
operator pointed at a fresh ``TASKQ_DB_URL`` does not have to run a separate
migration step before minting their first key — FR-07 owns the v1
migration for production deployments, but for an ad-hoc CLI invocation this
keeps the operator workflow to one command.

Citations:
- SPEC.md#L101-L107 (FR-03 — operator key minting, one-time plaintext)
- SPEC.md#L166 (NFR-04 — no internal leakage in operator output)
- SAD.md (§2.4 `taskq_api.__main__` — CLI dispatcher)
- SRS.md (AC-3.5 — `key create --scope <scope>`)
"""

from __future__ import annotations

import argparse
import sys

from taskq_api.models.orm import Base
from taskq_api.repository.session import get_engine, session_scope
from taskq_api.service import auth as auth_service

__all__ = ["main"]


def _ensure_schema() -> None:
    """Create the canonical schema if it does not yet exist.

    [FR-03] The CLI runs against whatever ``TASKQ_DB_URL`` points at —
    production deployments run the FR-07 migration script, but a first-time
    operator pointed at a fresh SQLite file would otherwise see
    ``OperationalError: no such table: api_keys``. ``create_all`` is
    idempotent so it is safe to call on every CLI invocation.
    """
    Base.metadata.create_all(get_engine())


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="python -m taskq_api",
        description="taskq-api operator CLI (FR-03)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    key_parser = subparsers.add_parser("key", help="API key operations")
    key_subparsers = key_parser.add_subparsers(dest="key_command", required=True)

    create = key_subparsers.add_parser(
        "create",
        help="Mint a new API key and print the plaintext to stdout.",
    )
    create.add_argument(
        "--scope",
        required=True,
        help="Scope label to attach to the new key (read/write/admin).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one operator command; return a Unix exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "key" and args.key_command == "create":
        _ensure_schema()
        with session_scope() as session:
            auth_service.create_api_key(
                scope=args.scope,
                session=session,
                plaintext_writer=print,
            )
        return 0

    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

# pragma: no error-handling
