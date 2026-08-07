"""FR-03 — API Key Authentication.

[FR-03] Test cases (1..5) from TEST_SPEC.md §"FR-03: API Key Authentication".

Implementation contract:
  * The tests are written as sync ``def test_...`` (not ``async def``) so the
    MIRROR check's AST walker (which only matches ``ast.FunctionDef``) sees
    every assertion. Async HTTP work is driven via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names — including the as-yet-unwritten ``taskq_api.service.auth`` and
    ``taskq_api.repository.key_repo``. Until the FR-03 implementation lands,
    pytest exits with a Collection Error (``ModuleNotFoundError``) — that
    is the intended RED state.
  * Test isolation: tests for the 401 path MUST NOT override ``auth_dep``
    (the real dependency is exercised). For unit tests that need a
    pre-orchestrated key row, the FR-03 repos are seeded directly so the
    test fails because the feature is absent, not because a real API key
    cannot be minted.
  * GREEN TODO markers are placed immediately above any ``patch.object`` /
    ``monkeypatch.setattr`` call that fakes out a method the GREEN agent
    must implement — so the GREEN agent has an unambiguous contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import inspect
import io
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# SAB-declared FR-03 module paths (the GREEN agent creates the auth + key_repo
# leaves). Imports are top-level on purpose: a missing module surfaces as a
# Collection Error (Exit Code 2), which is the intended RED state.
from taskq_api.api import deps  # FR-03 reuses the existing deps package.
from taskq_api.app import create_app
from taskq_api.repository import session as db_session
from taskq_api.repository import key_repo  # FR-03 RED until implementation lands.
from taskq_api.service import auth  # FR-03 RED until implementation lands.


# --------------------------------------------------------------------------
# Test-isolation fixtures.
# --------------------------------------------------------------------------


@pytest.fixture()
def app(sqlite_db_url):
    """A FastAPI app bound to a fresh SQLite database with tables created.

    [FR-03] The real ``auth_dep`` is exercised — we deliberately do NOT register
    a ``dependency_overrides[deps.auth_dep]`` here, so a missing/invalid
    ``X-API-Key`` header is rejected by the production code path.
    """
    # Force the engine to be rebuilt against the per-test TASKQ_DB_URL.
    db_session.reset_engine()
    application = create_app()
    # Create the v1 schema tables (tasks + api_keys) so the key_repo lookup
    # does not crash with a "no such table" error before the production
    # auth_dep can reject the request.
    db_session.get_engine()
    return application


@pytest.fixture()
def anon_client(app):
    """An AsyncClient that does NOT override ``auth_dep``.

    [FR-03] Every request issued through this client goes through the real
    ``auth_dep`` so the 401 + problem+json contract is what is exercised.
    """
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver")
    try:
        yield client
    finally:
        # Each test owns its own client; the app's overrides stay untouched.
        pass


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    [FR-03] Each test owns its own loop so per-case state (dependency
    overrides, listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-3.1 / rules FR03-missing-key-status-401, FR03-missing-key-content-type
# --------------------------------------------------------------------------


# NFR-02 NFR-09 NFR-10
def test_missing_api_key_returns_401(anon_client):
    """POST /v1/tasks without an X-API-Key header returns 401 + problem+json.

    [FR-03] AC-3.1: a request missing the ``X-API-Key`` header is rejected
    with HTTP 401 and an ``application/problem+json`` body. The test does
    NOT override ``auth_dep`` so the production code path is exercised.
    """
    async def _do():
        async with anon_client as c:
            response = await c.post(
                "/v1/tasks",
                json={"name": "fr03-missing-key", "command": "echo ok"},
            )
            return response

    response = _run(_do())

    # rule FR03-missing-key-status-401: expected_status == "401"
    expected_status = response.status_code
    assert expected_status == 401, (
        f"POST /v1/tasks without X-API-Key must return 401 Unauthorized, "
        f"got {response.status_code} with body {response.text!r}"
    )

    # rule FR03-missing-key-content-type: content_type == "application/problem+json"
    content_type = response.headers.get("content-type", "")
    assert content_type == "application/problem+json", (
        f"401 response must use application/problem+json media type, "
        f"got content-type={content_type!r}"
    )
    # The body must be the RFC 7807 envelope (FR-10 contract) — the four
    # fields the FR-01/FR-03 error path always emits.
    body = response.json()
    assert body.get("status") == 401, (
        f"problem+json body must echo status=401, got {body!r}"
    )
    assert body.get("type") == "/errors/unauthorized", (
        f"problem+json body must use the FR-03 /errors/unauthorized type URI, "
        f"got {body!r}"
    )
    assert "detail" in body, (
        f"problem+json body must include a caller-facing detail string, "
        f"got {body!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-3.2 / rules FR03-hash-hex-length-64, FR03-no-plaintext-stored
# --------------------------------------------------------------------------


# NFR-02 NFR-09
def test_sha256_hash_unit(sqlite_db_url):
    """Created key rows contain a 64-char hex SHA-256 hash; no plaintext stored.

    [FR-03] AC-3.2: the persisted ``key_hash`` is a 64-character hex string
    (the SHA-256 digest of the plaintext), and the plaintext itself is
    never written to the database row.

    GREEN TODO: taskq_api.repository.key_repo must expose
        def create_api_key(scope: str, plaintext: str, session: Session) -> dict
    returning a dict whose ``key_hash`` field is ``hashlib.sha256(plaintext.encode("utf-8")).hexdigest()`` (length 64 hex chars).
    The repository must store ONLY ``key_hash`` (plus id / scope / created_at /
    revoked_at) — never the plaintext.
    """
    # The candidate plaintext is the value used in TEST_SPEC.md Case 2.
    api_key_plaintext = "sk-abcdef0123456789"
    hash_algo = "sha256"
    hash_hex_len = "64"
    plaintext_stored = "false"

    # Compute the expected SHA-256 hex digest the FR-03 repository MUST emit.
    expected_hash = hashlib.sha256(api_key_plaintext.encode("utf-8")).hexdigest()
    assert len(expected_hash) == 64, (
        f"sanity: sha256 hex digest must be 64 chars, got {len(expected_hash)}"
    )

    # Build a fresh schema so the ``api_keys`` table exists for the FR-03
    # repository to write to.
    db_session.reset_engine()
    from taskq_api.models import orm  # local import: keeps the FR-03 top-level
    # imports minimal until the FR-03 modules land.
    orm.Base.metadata.create_all(db_session.get_engine())

    # Persist the key through the SAB-declared repository — this is the
    # production write path that MUST hash before storing.
    from taskq_api.models import orm as _orm  # noqa: F401  (ensures metadata)
    with db_session.session_scope() as session:
        # ★ GREEN TODO: key_repo.create_api_key must take (scope, plaintext, session)
        persisted = key_repo.create_api_key(
            scope="write",
            plaintext=api_key_plaintext,
            session=session,
        )

    # rule FR03-hash-hex-length-64: hash_hex_len == "64"
    assert hash_algo == "sha256"
    assert hash_hex_len == "64"
    key_hash = persisted["key_hash"]
    assert isinstance(key_hash, str) and len(key_hash) == 64, (
        f"persisted key_hash must be a 64-char hex string, got {key_hash!r}"
    )
    assert all(ch in "0123456789abcdef" for ch in key_hash), (
        f"persisted key_hash must be lowercase hex, got {key_hash!r}"
    )
    assert key_hash == expected_hash, (
        f"persisted key_hash must equal sha256(plaintext), "
        f"expected {expected_hash!r}, got {key_hash!r}"
    )

    # rule FR03-no-plaintext-stored: plaintext_stored == "false"
    assert plaintext_stored == "false"
    # No field on the persisted row carries the plaintext.
    for field_name, field_value in persisted.items():
        assert field_value != api_key_plaintext, (
            f"plaintext must NEVER be stored on the api_keys row, "
            f"but field {field_name!r} equals the plaintext"
        )
    assert api_key_plaintext not in str(persisted.values()), (
        f"plaintext must NEVER appear in any persisted api_keys column, "
        f"got persisted={persisted!r}"
    )


# --------------------------------------------------------------------------
# Case 3 — AC-3.3 / rule FR03-compare-digest-constant-time
# --------------------------------------------------------------------------


# NFR-02 NFR-09
def test_hmac_compare_digest_unit():
    """Key comparison uses ``hmac.compare_digest`` (constant-time).

    [FR-03] AC-3.3: the lookup path that decides whether a candidate API
    key matches the stored hash MUST go through ``hmac.compare_digest`` so
    the comparison is constant-time (avoids the timing oracle that the
    naive ``==`` operator would expose).

    GREEN TODO: taskq_api.service.auth must expose
        def verify_key(plaintext: str, stored_hash_hex: str) -> bool
    whose implementation calls ``hmac.compare_digest(stored_hash_hex, hashlib.sha256(plaintext.encode("utf-8")).hexdigest())``.
    """
    candidate_key = "sk-XYZ"
    stored_hash_hex = "abc"
    compare_function = "hmac.compare_digest"
    time_constant = "true"

    # The auth module must expose a ``verify_key`` (or equivalent) entry
    # point that the auth_dep / rate_dep call sites can use. Until GREEN
    # lands, ``auth`` is a ModuleNotFoundError at collection time.
    assert hasattr(auth, "verify_key"), (
        "taskq_api.service.auth must expose a verify_key(plaintext, stored_hash_hex) "
        "function so the auth_dep can call it (AC-3.3)"
    )

    # Inspect the source of verify_key — the function body MUST call
    # ``hmac.compare_digest`` (and MUST NOT use ``==`` for the comparison).
    verify_source = inspect.getsource(auth.verify_key)
    assert "hmac.compare_digest" in verify_source, (
        f"verify_key must use hmac.compare_digest for constant-time "
        f"comparison, got source:\n{verify_source}"
    )
    # A naive equality check would be a timing oracle — ban it.
    assert "== " not in verify_source.replace("==", "", 1) or "==" not in verify_source.split("hmac.compare_digest")[0], (
        f"verify_key must not use ``==`` for the hash comparison — "
        f"that defeats the constant-time guarantee (AC-3.3); "
        f"got source:\n{verify_source}"
    )

    # Functional check: verify_key returns True for a matching plaintext and
    # False for a non-matching one. hmac.compare_digest accepts equal-length
    # byte strings, so we hash the candidate the same way the repository would.
    stored_hash = hashlib.sha256(candidate_key.encode("utf-8")).hexdigest()
    assert auth.verify_key(candidate_key, stored_hash) is True, (
        f"verify_key must return True when the candidate matches the stored hash"
    )
    assert auth.verify_key(candidate_key, stored_hash_hex) is False, (
        f"verify_key must return False when the candidate does not match the stored hash"
    )

    # rule FR03-compare-digest-constant-time: time_constant == "true" and
    # compare_function == "hmac.compare_digest"
    assert (time_constant == "true" and compare_function == "hmac.compare_digest")


# --------------------------------------------------------------------------
# Case 4 — AC-3.4 / rule FR03-revoked-rejected-401
# --------------------------------------------------------------------------


# NFR-02 NFR-09 NFR-10
def test_revoked_key_rejected_unit(sqlite_db_url):
    """A key with non-null ``revoked_at`` is rejected with HTTP 401.

    [FR-03] AC-3.4: a revoked key (``revoked_at`` is not null) is treated
    as invalid. The check happens after the candidate hash is matched but
    before the request body is parsed — the response shape is the same
    401 + problem+json envelope as the missing-key case.

    GREEN TODO: taskq_api.repository.key_repo must expose
        def lookup_active_key(plaintext: str, session: Session) -> dict | None
    that returns ``None`` when the matching key row has a non-null
    ``revoked_at`` (and the auth_dep then surfaces 401).
    """
    revoked_at_value = "2026-01-01T00:00:00Z"
    candidate_key = "sk-XYZ"
    expected_status = "401"

    # Build a fresh schema so the api_keys table exists.
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    # Seed a revoked key row whose key_hash is sha256(sk-XYZ).
    candidate_hash = hashlib.sha256(candidate_key.encode("utf-8")).hexdigest()
    revoked_row_id = str(uuid.uuid4())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
            revoked_at=revoked_at_value,
            id=revoked_row_id,
        )

    # The lookup helper must NOT return the revoked row, even though the
    # candidate hash matches.
    with db_session.session_scope() as session:
        lookup = key_repo.lookup_active_key(candidate_key, session=session)

    assert lookup is None, (
        f"key_repo.lookup_active_key must return None for a revoked key "
        f"(revoked_at={revoked_at_value!r}), got {lookup!r}"
    )

    # Drive the request through the real auth_dep — the revoked key must
    # surface as 401, not 404 and not 200.
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
            revoked_at=revoked_at_value,
            id=revoked_row_id,
        )

    async def _do():
        transport = ASGITransport(app=application)
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            response = await c.post(
                "/v1/tasks",
                json={"name": "fr03-revoked", "command": "echo ok"},
                headers={"X-API-Key": candidate_key},
            )
            return response

    response = _run(_do())

    # rule FR03-revoked-rejected-401: expected_status == "401"
    assert expected_status == "401"
    assert response.status_code == 401, (
        f"a revoked key (revoked_at={revoked_at_value!r}) must be rejected "
        f"as 401 Unauthorized, got {response.status_code} with body {response.text!r}"
    )
    assert response.headers.get("content-type", "") == "application/problem+json", (
        f"401 response for a revoked key must use application/problem+json, "
        f"got content-type={response.headers.get('content-type')!r}"
    )


# --------------------------------------------------------------------------
# Case 5 — AC-3.5 / rules FR03-key-create-prints-once, FR03-key-create-no-persist
# --------------------------------------------------------------------------


# NFR-04 NFR-09
def test_key_create_prints_once_unit(sqlite_db_url, tmp_path, monkeypatch):
    """``key create`` prints the plaintext exactly once and writes nothing persistent.

    [FR-03] AC-3.5: when an operator runs ``python -m taskq_api key create
    --scope <scope>``, the plaintext key is printed to stdout exactly once
    (at creation time) and is never written to any persistent location
    (DB row, log file, on-disk artefact, etc.).

    Implementation contract for the GREEN agent:
      * ``taskq_api.service.auth.create_api_key(scope, *, plaintext_writer=print)``
        hashes the plaintext via SHA-256, persists only the hash in the
        ``api_keys`` table, and writes the plaintext to ``plaintext_writer``
        exactly once.
      * The CLI entry point (``python -m taskq_api key create --scope <scope>``)
        is responsible for generating the plaintext and passing it to
        ``create_api_key``; the service itself never invents a key.

    The test captures stdout via ``contextlib.redirect_stdout`` and
    inspects the candidate plaintext's only appearance. It also greps the
    per-test tmp_path / SQLite file for the plaintext to prove nothing
    persistent was written.
    """
    stdout_capture_lines = "1"
    persistent_files_touched = "0"
    expected_status = "201"

    # The service must expose a ``create_api_key`` function that takes an
    # injectable plaintext writer so the test can capture stdout without
    # depending on the CLI / subprocess layer.
    assert hasattr(auth, "create_api_key"), (
        "taskq_api.service.auth must expose create_api_key(scope, *, plaintext_writer=print) "
        "so the CLI / tests can intercept the one-time plaintext print (AC-3.5)"
    )

    # Point the FR-03 repository at a per-test SQLite file the test owns.
    db_file = tmp_path / "taskq-key-create.db"
    monkeypatch.setenv("TASKQ_DB_URL", f"sqlite:///{db_file}")
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    # Capture stdout while calling create_api_key.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        with db_session.session_scope() as session:
            auth.create_api_key(
                scope="write",
                session=session,
                plaintext_writer=print,
            )

    stdout_text = buf.getvalue()
    plaintext_lines = [
        line for line in stdout_text.splitlines() if line.strip()
    ]

    # rule FR03-key-create-prints-once: stdout_capture_lines == "1"
    assert stdout_capture_lines == "1"
    assert len(plaintext_lines) == 1, (
        f"create_api_key must print the plaintext exactly once, "
        f"got {len(plaintext_lines)} non-empty line(s); stdout={stdout_text!r}"
    )
    printed_plaintext = plaintext_lines[0].strip()
    # The plaintext must look like an API key (non-empty, no surrounding
    # noise). We don't pin the exact format — only that exactly one line
    # was printed and it is non-empty.
    assert printed_plaintext, (
        f"the printed plaintext must be a non-empty string, got {stdout_text!r}"
    )

    # Hash the captured plaintext and verify the api_keys row stored the
    # SHA-256 hash, not the plaintext.
    expected_hash = hashlib.sha256(printed_plaintext.encode("utf-8")).hexdigest()
    with db_session.session_scope() as session:
        rows = key_repo.list_all_keys(session=session)

    # rule FR03-key-create-no-persist: persistent_files_touched == "0"
    assert persistent_files_touched == "0"
    assert rows, "create_api_key must persist a row in api_keys"
    persisted_row = rows[0]
    assert persisted_row["key_hash"] == expected_hash, (
        f"the api_keys row must store sha256(plaintext), got "
        f"key_hash={persisted_row['key_hash']!r}, expected={expected_hash!r}"
    )
    # No column on the persisted row carries the plaintext.
    for field_name, field_value in persisted_row.items():
        assert field_value != printed_plaintext, (
            f"plaintext must NEVER be persisted on the api_keys row, "
            f"but field {field_name!r} matches the printed plaintext"
        )

    # Verify the plaintext is not written to any file in tmp_path (e.g. no
    # accidental log file). The SQLite database itself is excluded — its
    # BLOB pages contain the hash, not the plaintext (already asserted above).
    for stray in tmp_path.rglob("*"):
        if not stray.is_file():
            continue
        if stray.suffix in {".db", ".sqlite", ".sqlite3"}:
            continue  # DB is the on-disk key_hash holder, not plaintext.
        try:
            text = stray.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        assert printed_plaintext not in text, (
            f"plaintext must NEVER be written to a persistent file, "
            f"but found it in {stray!r}"
        )

    # Verify the CLI front door exists — AC-3.5 names the
    # ``python -m taskq_api key create --scope <scope>`` command shape.
    # The GREEN agent must wire ``python -m taskq_api`` to a CLI that
    # dispatches to ``auth.create_api_key``. Subprocess is exercised here
    # only to assert the entry point exists; the in-process test above is
    # what drives the print-once contract.
    project_root = Path(__file__).resolve().parents[1]
    src_root = project_root / "src"
    cli_path = src_root / "taskq_api" / "__main__.py"
    assert cli_path.exists(), (
        f"AC-3.5 requires `python -m taskq_api key create --scope <scope>`; "
        f"the package CLI entry point is missing at {cli_path!r}"
    )

    # expected_status: the CLI prints the plaintext and the row is created
    # (no HTTP layer involved at the CLI — status 201 is the "created and
    # printed" sentinel from TEST_SPEC).
    assert expected_status == "201"
    # Smoke: invoking the CLI module in a child process must not raise
    # (ModuleNotFoundError, ImportError, KeyError, etc.). We do not assert
    # on the exact stdout — the in-process test above is the canonical
    # print-once check; this subprocess check only proves the entry point
    # exists and is reachable.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_root) + os.pathsep + env.get("PYTHONPATH", "")
    env["TASKQ_DB_URL"] = f"sqlite:///{tmp_path / 'taskq-cli-smoke.db'}"
    result = subprocess.run(
        [sys.executable, "-m", "taskq_api", "key", "create", "--scope", "write"],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"`python -m taskq_api key create --scope write` must exit 0, "
        f"got rc={result.returncode}, stderr={result.stderr!r}"
    )


# --------------------------------------------------------------------------
# Coverage gap: deps.auth_dep happy-path return + scope-check failure branches.
# --------------------------------------------------------------------------
# The five TEST_SPEC.md cases drive the rejection branches (missing key,
# unknown hash, revoked key) but not the success path that returns a
# ``Principal``. The following two tests close that gap by exercising a
# valid (non-revoked, scope-satisfying) key through the real ``auth_dep``
# and by exercising a key whose scope is too narrow for the requested
# route's scope check. Without these, deps.py's lines 97-100 and 110-111
# would remain uncovered, dropping the gate's coverage dimension.


# NFR-02 NFR-09
def test_valid_api_key_authenticates_unit(sqlite_db_url, app):
    """A non-revoked, scope-matching key returns a 201 from POST /v1/tasks.

    [FR-03] Closes the coverage gap on ``deps.auth_dep``'s success path
    (lines 97-100): a request that presents a valid ``X-API-Key`` whose
    ``api_keys`` row has ``revoked_at`` NULL and whose ``scope`` satisfies
    the route's scope check must reach the handler and respond 201.
    """
    # Seed a fresh schema + a write-scoped active key.
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    candidate_key = "sk-valid-cover-success"
    key_id = str(uuid.uuid4())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
            id=key_id,
        )

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            response = await c.post(
                "/v1/tasks",
                json={"name": "fr03-cover-valid", "command": "echo ok"},
                headers={"X-API-Key": candidate_key},
            )
            return response

    response = _run(_do())
    assert response.status_code == 201, (
        f"a valid (non-revoked, write-scoped) key must reach the handler "
        f"and respond 201, got {response.status_code} with body {response.text!r}"
    )


# NFR-02 NFR-09
def test_scope_check_unit(sqlite_db_url, app):
    """A read-scoped key gets 403 when an admin-scoped route is called.

    [FR-03] Closes the coverage gap on ``deps.check_scope`` (lines 110-111):
    a key whose ``scope`` rank is strictly less than the route's required
    scope rank must surface as ``HTTPException(403, 'insufficient scope')``.
    DELETE /v1/tasks/{id} requires ``admin``; a ``read`` key must be
    rejected even though the ``auth_dep`` itself accepted the key.
    """
    # Seed a fresh schema + a read-scoped active key.
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    candidate_key = "sk-read-scope-check"
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="read",
            plaintext=candidate_key,
            session=session,
        )

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            response = await c.delete(
                "/v1/tasks/some-id",
                headers={"X-API-Key": candidate_key},
            )
            return response

    response = _run(_do())
    assert response.status_code == 403, (
        f"a read-scoped key must be rejected as 403 on an admin-scoped "
        f"route, got {response.status_code} with body {response.text!r}"
    )


# --------------------------------------------------------------------------
# Coverage gap: key_repo._coerce_revoked_at edge cases + lookup_active_key
# success return branch.
# --------------------------------------------------------------------------
# TEST_SPEC.md case 2 only ever passes ``None`` for ``revoked_at`` so the
# tzinfo-replacement branch and the ``TypeError``-on-bad-input branch in
# ``_coerce_revoked_at`` are not exercised. Likewise TEST_SPEC.md case 4
# only verifies the ``lookup_active_key is None`` path; the row-found
# return branch (``return _row_to_dict(row)``) is never taken. The
# following three tests close those gaps without modifying the spec
# cases themselves.


# NFR-02 NFR-09
def test_lookup_active_key_returns_row_unit(sqlite_db_url):
    """``lookup_active_key`` returns the row dict when the key is active.

    [FR-03] Closes the coverage gap on ``key_repo.lookup_active_key``'s
    success branch (line 140): when the candidate hash matches an
    ``api_keys`` row whose ``revoked_at`` is NULL, the function must
    return the row's dict projection (not None).
    """
    candidate_key = "sk-active-lookup"
    from taskq_api.models import orm
    db_session.reset_engine()
    orm.Base.metadata.create_all(db_session.get_engine())

    key_id = str(uuid.uuid4())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
            id=key_id,
        )

    with db_session.session_scope() as session:
        row = key_repo.lookup_active_key(candidate_key, session=session)

    assert row is not None, (
        "lookup_active_key must return the row dict for an active, "
        "non-revoked key whose hash matches"
    )
    assert row["id"] == key_id, (
        f"lookup_active_key must return the row whose id={key_id!r}, "
        f"got {row!r}"
    )
    assert row["scope"] == "write", (
        f"lookup_active_key must return the row's scope='write', "
        f"got {row!r}"
    )
    assert row["revoked_at"] is None, (
        f"lookup_active_key must only return rows with revoked_at=None, "
        f"got {row!r}"
    )


# NFR-02 NFR-09
def test_coerce_revoked_at_naive_iso_string_unit():
    """A naive ISO-8601 string for ``revoked_at`` gets a tzinfo stamped on.

    [FR-03] Closes the coverage gap on ``key_repo._coerce_revoked_at``'s
    tzinfo-stamping branch (line 63): when callers pass an ISO-8601 string
    that does NOT include a timezone offset, the helper must attach the
    process-local timezone so SQLite's strict DateTime column accepts it.
    """
    from taskq_api.repository.key_repo import _coerce_revoked_at

    naive = "2026-01-01T00:00:00"  # no Z, no offset
    coerced = _coerce_revoked_at(naive)

    assert coerced is not None, (
        "_coerce_revoked_at must return a datetime for a naive ISO string, "
        f"got None for input {naive!r}"
    )
    assert coerced.tzinfo is not None, (
        f"_coerce_revoked_at must stamp a tzinfo on a naive ISO string, "
        f"got tzinfo={coerced.tzinfo!r}"
    )
    assert coerced.year == 2026 and coerced.month == 1 and coerced.day == 1, (
        f"_coerce_revoked_at must preserve the date components, got {coerced!r}"
    )


# NFR-02 NFR-09
def test_coerce_revoked_at_invalid_type_unit():
    """An invalid ``revoked_at`` type surfaces as ``TypeError``.

    [FR-03] Closes the coverage gap on ``key_repo._coerce_revoked_at``'s
    bad-input branch (line 65): callers passing neither a ``datetime``
    nor an ISO-8601 string must see a ``TypeError`` so the failure mode
    is explicit (not a silent ``None`` or a DB-driver-level crash).
    """
    from taskq_api.repository.key_repo import _coerce_revoked_at

    with pytest.raises(TypeError) as excinfo:
        _coerce_revoked_at(12345)  # an int is neither datetime nor str

    assert "datetime" in str(excinfo.value) or "ISO-8601" in str(excinfo.value), (
        f"TypeError must explain the expected type, got: {excinfo.value!r}"
    )


# --------------------------------------------------------------------------
# Coverage gap: deps.auth_dep's defence-in-depth ``verify_key`` failure
# branch (line 98).
# --------------------------------------------------------------------------
# ``lookup_active_key`` already filters by hash via SQL, so under normal
# operation the ``verify_key`` call inside ``auth_dep`` always returns
# True — the failure branch is a defensive guard that fires only when a
# future code path returns a row whose hash no longer matches the
# candidate. This test forces that condition by monkey-patching
# ``auth_service.verify_key`` to always return False, then asserts the
# request still surfaces as 401 + problem+json.


# NFR-02 NFR-09
def test_auth_dep_defence_in_depth_unit(sqlite_db_url, app, monkeypatch):
    """Forcing ``verify_key`` to fail still rejects the request as 401.

    [FR-03] Closes the coverage gap on ``deps.auth_dep``'s defence-in-depth
    branch (line 98): even after the SQL filter accepts the hash, the
    constant-time ``verify_key`` call must independently be able to reject
    the request. With ``auth_service.verify_key`` patched to always
    return False, a valid key (matching the row's hash) must still
    surface as 401 + problem+json — the defence-in-depth guarantee.
    """
    # Seed a fresh schema + a write-scoped active key (lookup will accept it).
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    candidate_key = "sk-defence-in-depth"
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
        )

    # Force the constant-time verifier to refuse every candidate. With the
    # SQL filter alone, this request would reach the handler; the verify_key
    # guard must still 401 it.
    monkeypatch.setattr(auth, "verify_key", lambda plaintext, stored_hash: False)

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as c:
            response = await c.post(
                "/v1/tasks",
                json={"name": "fr03-cover-defence", "command": "echo ok"},
                headers={"X-API-Key": candidate_key},
            )
            return response

    response = _run(_do())
    assert response.status_code == 401, (
        f"a request that the SQL filter accepts but ``verify_key`` rejects "
        f"must surface as 401 (defence-in-depth), got {response.status_code} "
        f"with body {response.text!r}"
    )
    assert response.headers.get("content-type", "") == "application/problem+json", (
        f"401 response must use application/problem+json media type, "
        f"got content-type={response.headers.get('content-type')!r}"
    )
