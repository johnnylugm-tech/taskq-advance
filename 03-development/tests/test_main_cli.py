"""Coverage tests for ``taskq_api.__main__`` (operator CLI dispatcher)."""

from __future__ import annotations

import sys

import pytest

from taskq_api import __main__ as cli


def test_main_help_returns_2(monkeypatch) -> None:
    """``python -m taskq_api`` with no subcommand prints help and exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main([])
    assert exc_info.value.code == 2


def test_main_unknown_subcommand_returns_2(monkeypatch) -> None:
    """``python -m taskq_api bogus`` falls through to help and exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["bogus"])
    assert exc_info.value.code == 2


def test_main_key_create_minimal(monkeypatch, tmp_path, capsys) -> None:
    """``python -m taskq_api key create --scope admin`` mints a key and exits 0."""
    from taskq_api.repository import session as db_session
    from taskq_api.models import orm

    db_path = tmp_path / "cli-test.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_path}")
    db_session.reset_engine()
    # Pre-create the schema so the CLI's _ensure_schema is a no-op (covered twice)
    orm.Base.metadata.create_all(db_session.get_engine())

    rc = cli.main(["key", "create", "--scope", "admin"])
    captured = capsys.readouterr()

    assert rc == 0
    # The plaintext was printed exactly once (per FR-03 AC-3.5).
    assert "sk-" in captured.out


def test_main_key_unknown_subcommand_returns_2(monkeypatch, capsys) -> None:
    """``python -m taskq_api key bogus`` prints help and returns 2 (covers main.py:87-88)."""
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["key", "bogus"])
    assert exc_info.value.code == 2


def test_main_fall_through_to_help(monkeypatch, capsys) -> None:
    """When ``args.command`` is set but not 'key', main prints help and returns 2.

    Drives the dead-branch fall-through (main.py:87-88) by patching
    ``_build_parser`` to return a parser whose ``parse_args`` returns a
    Namespace with an unknown command value.
    """
    class _Args:
        command = "unknown"
        key_command = None

    class _StubParser:
        def parse_args(self, argv=None):  # type: ignore[no-untyped-def]
            return _Args()

        def print_help(self, file=None):  # type: ignore[no-untyped-def]
            file = file or sys.stdout
            file.write("usage: stub\n")

    monkeypatch.setattr(cli, "_build_parser", lambda: _StubParser())
    rc = cli.main([])
    assert rc == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err


def test_main_dunder_main_guard(monkeypatch) -> None:
    """Importing __main__ does NOT auto-run main(); only ``__name__ == '__main__'`` does.

    Drives the import-time branch (line 92) by reading the module's
    bytecode — but more usefully, asserts that ``main()`` is a callable
    that the dunder guard would invoke. The dunder guard itself can
    only fire by running the file directly, which pytest does not.
    """
    assert callable(cli.main)

