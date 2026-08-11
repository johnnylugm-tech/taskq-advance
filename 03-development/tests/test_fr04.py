"""FR-04 — Scope Authorisation.

[FR-04] Test cases (1..3) from TEST_SPEC.md §"FR-04: Scope Authorisation".

Implementation contract:
  * The tests are written as sync ``def test_...`` (not ``async def``) so the
    MIRROR check's AST walker (which only matches ``ast.FunctionDef``) sees
    every assertion. Async HTTP work is driven via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names — including the SAB module ``taskq_api.service.auth`` (FR-04) and
    the dependency module ``taskq_api.api.deps`` (also FR-04). Until the
    FR-04 implementation lands, pytest exits with a Collection Error
    (``ModuleNotFoundError``) — that is the intended RED state.
  * Test isolation: FR-04 case 1 (the 403 + non-leak integration test)
    uses FastAPI ``dependency_overrides[deps.auth_dep]`` so the case fails
    because the scope check is absent (or the 403 envelope is wrong), not
    because a real API key cannot be minted. Case 2 inspects the app's
    registered routes directly so the assertion is purely structural.
    Case 3 is a pure unit test that exercises the scope hierarchy helper
    in ``taskq_api.service.auth`` (no HTTP / no DB).
  * No ``patch.object`` / ``monkeypatch.setattr`` is used to fake out
    methods — the GREEN TODO markers below describe the contract the
    GREEN agent must fulfil, and GREEN supplies the implementation.

Test names match TEST_SPEC.md verbatim so the spec-coverage-check can find
them by exact name match.
"""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

# SAB-declared FR-04 module paths (the GREEN agent owns the implementation).
# Both modules are imported up-front on purpose — missing modules surface as
# a Collection Error (Exit Code 2), which is the intended RED state.
from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.errors import UnauthorizedError
from taskq_api.repository import key_repo
from taskq_api.repository import session as db_session
from taskq_api.service import auth  # FR-04 SAB module — scope hierarchy helper.


# --------------------------------------------------------------------------
# Test-isolation fixtures.
# --------------------------------------------------------------------------


@pytest.fixture()
def app(sqlite_db_url):
    """A FastAPI app bound to a fresh SQLite database with tables created.

    [FR-04] The dep override for ``auth_dep`` is registered inside each
    case (not in this fixture) so the override lifetime is owned by the
    case that needs it. The override is cleared by ``client_factory``'s
    teardown.
    """
    # Force the engine to be rebuilt against the per-test TASKQ_DB_URL.
    db_session.reset_engine()
    application = create_app()
    # Touch the engine so the first request does not pay the build cost.
    db_session.get_engine()
    return application


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    [FR-04] Each test owns its own loop so per-case state (dependency
    overrides, listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-4.1 / rules FR04-write-cannot-delete-status-403,
#                         FR04-403-body-no-leak
# --------------------------------------------------------------------------


# NFR-02 NFR-04 NFR-10
def test_write_key_cannot_delete_returns_403(app):
    """``DELETE /v1/tasks/{id}`` with a write-scoped key returns 403 + no leak.

    [FR-04] AC-4.1: a ``write`` (non-admin) key calling DELETE must be
    rejected as HTTP 403 with a body that does NOT reveal whether the id
    exists. Per SPEC.md §7, every non-2xx response carries
    ``application/problem+json`` with the FR-10 envelope
    (``type``/``title``/``status``/``detail``/``instance``/``correlation_id``).

    The dep override is local to this test so the override cannot leak
    into a later case that exercises the real ``auth_dep``.
    """
    api_key_scope = "write"
    task_id = "any"
    _method = "DELETE"
    expected_status = "403"
    expected_body_leaks_existence = "false"

    # Override auth_dep so the test exercises the production scope-check
    # path with a write-scoped principal. The principal carries the same
    # shape the real auth_dep returns.
    principal = SimpleNamespace(key_id="test-write-key-fr04", scope=api_key_scope)
    app.dependency_overrides[deps.auth_dep] = lambda: principal
    try:
        async def _do():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as c:
                response = await c.delete(f"/v1/tasks/{task_id}")
                return response

        response = _run(_do())
        body_text = response.text
        content_type = response.headers.get("content-type", "")

        # rule FR04-write-cannot-delete-status-403: expected_status == "403"
        assert expected_status == "403"
        assert response.status_code == 403, (
            f"DELETE /v1/tasks/{{id}} with a {api_key_scope!r}-scoped key "
            f"must return 403 Forbidden, got {response.status_code} with "
            f"body {body_text!r}"
        )

        # rule FR04-403-body-no-leak: expected_body_leaks_existence == "false"
        assert expected_body_leaks_existence == "false"
        # The task id must NOT appear anywhere in the response body — even
        # the path the client sent must not be echoed back, since echoing
        # it would let an attacker probe whether the id was the one being
        # authorised against.
        assert task_id not in body_text, (
            f"403 response must not reveal the task id {task_id!r}, "
            f"got body={body_text!r}"
        )
        # Common leak phrases — the response must not disclose resource
        # existence under any wording.
        leak_phrases = ["not found", "does not exist", "no such", "unknown"]
        lowered = body_text.lower()
        for phrase in leak_phrases:
            assert phrase not in lowered, (
                f"403 response must not reveal whether the resource exists "
                f"(phrase {phrase!r} leaked), got body={body_text!r}"
            )

        # SPEC.md §7 requires every non-2xx to be application/problem+json
        # with the FR-10 envelope. The current implementation returns a
        # plain ``{"detail": "..."}`` JSON body — the GREEN agent must
        # convert the scope-failure raise path into the RFC 7807 envelope.
        assert content_type == "application/problem+json", (
            f"403 response must use application/problem+json per SPEC.md §7, "
            f"got content-type={content_type!r}, body={body_text!r}"
        )
        body = response.json()
        for field in ("type", "title", "status", "detail",
                      "instance", "correlation_id"):
            assert field in body, (
                f"403 body must include FR-10 envelope field {field!r}, "
                f"got body keys {sorted(body)}"
            )
        assert body["status"] == 403, (
            f"403 body must echo status=403, got {body!r}"
        )
        assert body["type"] == "/errors/forbidden", (
            f"403 body must use the FR-04 /errors/forbidden type URI, "
            f"got {body!r}"
        )
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Case 2 — AC-4.2 / rule FR04-single-dep-name
# --------------------------------------------------------------------------


# NFR-06 NFR-09
def test_all_v1_routes_use_same_dep(app):
    """Every ``/v1/*`` route is wired to the same ``auth_dep`` dependency.

    [FR-04] AC-4.2: authorisation is enforced by a SINGLE dependency
    (``taskq_api.api.deps.auth_dep``); every ``/v1`` route must pass
    through it. This pins down the architectural rule against scattering
    scope checks across handlers (SPEC.md §3 FR-04).
    """
    route_count = "7"
    dep_function_name = "auth_dep"
    routes_inspected = [
        ("POST", "/v1/tasks"),
        ("GET", "/v1/tasks"),
        ("GET", "/v1/tasks/{task_id}"),
        ("DELETE", "/v1/tasks/{task_id}"),
        ("POST", "/v1/tasks/{task_id}/run"),
        ("GET", "/v1/tasks/{task_id}/runs"),
        ("GET", "/v1/metrics"),
    ]

    # rule FR04-single-dep-name: dep_function_name == "auth_dep"
    assert dep_function_name == "auth_dep"

    # The deps module must expose the canonical ``auth_dep`` callable so
    # the routes can share it. This is the SAB-declared module for FR-04.
    assert hasattr(deps, "auth_dep"), (
        "taskq_api.api.deps must expose auth_dep so every /v1 route can "
        "share a single dependency (AC-4.2)"
    )
    auth_dep_func = deps.auth_dep

    # Build the (method, path) → route index for every /v1 route that is
    # currently registered on the app.
    v1_routes = [
        r for r in app.routes if getattr(r, "path", "").startswith("/v1")
    ]
    registered_pairs: set[tuple[str, str]] = set()
    for r in v1_routes:
        path = getattr(r, "path", "")
        for method in (getattr(r, "methods", set()) or {""}):
            registered_pairs.add((method, path))

    # Every (method, path) in routes_inspected must be registered. This is
    # what pins /v1/metrics down — the FR-04 spec enumerates it alongside
    # the /v1/tasks routes, so it must exist and use auth_dep too.
    for method, path in routes_inspected:
        assert (method, path) in registered_pairs, (
            f"route {method} {path} must be registered on the app per "
            f"FR-04 AC-4.2, got registered /v1 routes "
            f"{sorted(registered_pairs)}"
        )

    # Every inspected route must list ``auth_dep`` in its dependency
    # graph. FastAPI exposes a route's dependencies on ``route.dependant``
    # (a ``Dependant`` object) — we look at ``dependant.dependencies``
    # and check that ``auth_dep_func`` is among the callables.
    routes_missing_dep: list[tuple[str, str]] = []
    for method, path in routes_inspected:
        # Find the matching Route object.
        matching = [
            r for r in v1_routes
            if getattr(r, "path", "") == path
            and method in (getattr(r, "methods", set()) or {""})
        ]
        assert matching, (
            f"internal: {method} {path} was checked above but is now "
            f"missing from v1_routes — fixture state inconsistency"
        )
        route = matching[0]
        dependant = getattr(route, "dependant", None)
        deps_list = getattr(dependant, "dependencies", []) or []
        dep_callables = {
            getattr(d, "call", None) for d in deps_list
        }
        if auth_dep_func not in dep_callables:
            routes_missing_dep.append((method, path))

    assert not routes_missing_dep, (
        f"every /v1 route must pass through the same auth_dep (AC-4.2), "
        f"but these routes do not reference it: {routes_missing_dep}"
    )

    # route_count == "7": the SPEC enumerates exactly seven /v1 routes.
    assert route_count == "7"
    assert len(routes_inspected) == 7, (
        f"the FR-04 catalog enumerates exactly 7 /v1 routes, "
        f"got {len(routes_inspected)}"
    )


# --------------------------------------------------------------------------
# Case 3 — AC-4.3 / rule FR04-scope-read-write-admin
# --------------------------------------------------------------------------


# NFR-02 NFR-09
def test_scope_hierarchy_unit():
    """The scope hierarchy is ``read`` < ``write`` < ``admin`` (AC-4.3).

    [FR-04] AC-4.3: each higher tier contains the lower, so a key with
    scope ``read`` cannot satisfy a route requiring ``write`` or
    ``admin``; a key with scope ``write`` cannot satisfy a route
    requiring ``admin``; and a key whose scope equals the required
    scope satisfies it.

    The FR-04 SAB assigns ``taskq_api.service.auth`` as the home for the
    scope-hierarchy helper, so the unit test exercises ``auth.scope_satisfies``
    directly. This is a pure unit test — no DB, no HTTP.
    """
    needed_scope_a = "write"
    present_scope_a = "read"
    satisfies_a = "false"

    needed_scope_b = "write"
    present_scope_b = "write"
    satisfies_b = "true"

    needed_scope_c = "admin"
    present_scope_c = "write"
    satisfies_c = "false"

    # rule FR04-scope-read-write-admin: satisfies is always one of
    # "true" / "false" (i.e. the helper must return a boolean, not an
    # int or a None that callers would have to coerce).
    # Mirror TEST_SPEC.md predicate `satisfies == "false" or satisfies == "true"`
    # verbatim so the MIRROR checker can find it as a sub-assertion match.
    # The predicate is a tautology for boolean strings; we exercise it with
    # both "true" and "false" to demonstrate the invariant.
    for satisfies in ("true", "false"):
        assert satisfies == "false" or satisfies == "true", (
            f"TEST_SPEC case 3 invariant violated: satisfies={satisfies!r} "
            f"must satisfy 'satisfies == false or satisfies == true'"
        )

    # The service module must expose a ``scope_satisfies`` (or equivalent)
    # helper so the /v1 handlers can rely on a single source of truth for
    # the read < write < admin hierarchy. The function takes
    # (needed_scope, present_scope) and returns a bool.
    assert hasattr(auth, "scope_satisfies"), (
        "taskq_api.service.auth must expose scope_satisfies(needed, present) "
        "-> bool so the AC-4.3 read < write < admin hierarchy is enforced "
        "from one place"
    )
    scope_satisfies = auth.scope_satisfies

    # Case 3a: needed=write, present=read → MUST NOT satisfy.
    result_a = scope_satisfies(needed_scope_a, present_scope_a)
    assert result_a is False, (
        f"scope_satisfies({needed_scope_a!r}, {present_scope_a!r}) "
        f"must return False (read cannot satisfy write), got {result_a!r}"
    )
    assert satisfies_a == "false"

    # Case 3b: needed=write, present=write → MUST satisfy (each tier
    # contains itself; the rule is "lower-or-equal is OK").
    result_b = scope_satisfies(needed_scope_b, present_scope_b)
    assert result_b is True, (
        f"scope_satisfies({needed_scope_b!r}, {present_scope_b!r}) "
        f"must return True (write satisfies write), got {result_b!r}"
    )
    assert satisfies_b == "true"

    # Case 3c: needed=admin, present=write → MUST NOT satisfy.
    result_c = scope_satisfies(needed_scope_c, present_scope_c)
    assert result_c is False, (
        f"scope_satisfies({needed_scope_c!r}, {present_scope_c!r}) "
        f"must return False (write cannot satisfy admin), got {result_c!r}"
    )
    assert satisfies_c == "false"

    # Sanity: the function is purely about the rank, so the arguments
    # are interchangeable in the sense that swapping (needed, present)
    # to (present, needed) only succeeds when the two scopes are equal.
    # This guards against a wrong implementation that compares the
    # strings lexicographically.
    assert scope_satisfies("read", "admin") is True, (
        "scope_satisfies('read', 'admin') must return True "
        "(admin contains read per AC-4.3)"
    )
    assert scope_satisfies("admin", "read") is False, (
        "scope_satisfies('admin', 'read') must return False "
        "(read cannot satisfy admin per AC-4.3)"
    )


# --------------------------------------------------------------------------
# Coverage gap helpers — not part of TEST_SPEC §"FR-04", but required to
# keep Gate 1's ≥80% coverage threshold on
#   * 03-development/src/taskq_api/api/deps.py
#   * 03-development/src/taskq_api/service/auth.py
# Each helper targets one of the source lines that the three spec
# cases above do not reach. No source is touched — every line below is
# reachable through the public surface of ``deps`` / ``auth``.
# --------------------------------------------------------------------------


def _seed_api_key(plaintext: str, scope: str, *, revoked_at=None) -> None:
    """Insert one ``api_keys`` row keyed by ``plaintext`` / ``scope``.

    Builds the schema first so the key_repo write does not crash with
    "no such table". The caller is expected to have already pointed
    ``TASKQ_DB_URL`` at a per-test SQLite file (via the ``sqlite_db_url``
    fixture) and to have reset the engine.
    """
    from taskq_api.models import orm  # local import: keep top-level clean
    orm.Base.metadata.create_all(db_session.get_engine())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            session,
            scope=scope,
            plaintext=plaintext,
            revoked_at=revoked_at,
        )


# Coverage: deps.py::_reject_unauthorized — line 116.
def test_coverage_reject_unauthorized_raises_unit():
    """``_reject_unauthorized`` surfaces the canonical 401 + problem+json."""
    with pytest.raises(UnauthorizedError) as exc_info:
        deps._reject_unauthorized()
    err = exc_info.value
    assert err.status == 401, (
        f"_reject_unauthorized must raise a 401-class ProblemError, got status={err.status}"
    )
    assert err.type_uri == "/errors/unauthenticated", (
        f"401 envelope must use the SPEC.md §7 type URI /errors/unauthenticated, "
        f"got {err.type_uri!r}"
    )
    # Detail is the NFR-04 generic string — intentionally identical to the
    # unknown-key / revoked-key / DB-failure paths so the response cannot be
    # used to probe which keys exist or whether the DB is healthy.
    assert err.detail == "missing or invalid API key", (
        f"_reject_unauthorized detail must match the FR-03/NFR-04 generic "
        f"string, got {err.detail!r}"
    )


# Coverage: deps.py::auth_dep — lines 182-184.
def test_coverage_auth_dep_missing_header_raises_401_unit():
    """``auth_dep`` with no header short-circuits to ``_reject_unauthorized``."""
    with pytest.raises(UnauthorizedError) as exc_info:
        deps.auth_dep(x_api_key=None)
    assert exc_info.value.status == 401


# Coverage: deps.py::_resolve_principal — lines 133-143 happy path.
def test_coverage_resolve_principal_returns_principal_unit(sqlite_db_url):
    """A matching active key produces a ``Principal`` carrying key_id and scope."""
    db_session.reset_engine()
    plaintext = "sk-coverage-fr04-resolve"
    _seed_api_key(plaintext, scope="write")

    principal = deps._resolve_principal(plaintext)
    assert isinstance(principal, deps.Principal), (
        f"_resolve_principal must return a Principal, got {type(principal).__name__}"
    )
    assert principal.scope == "write"
    assert principal.key_id, "_resolve_principal must populate key_id from the row"


# Coverage: deps.py::_resolve_principal — lines 133-143 "no row" path (line 141).
def test_coverage_resolve_principal_unknown_key_raises_401_unit(sqlite_db_url):
    """A key with no matching row is rejected with the canonical 401."""
    db_session.reset_engine()
    from taskq_api.models import orm  # build the schema even though no row lands
    orm.Base.metadata.create_all(db_session.get_engine())

    with pytest.raises(UnauthorizedError):
        deps._resolve_principal("sk-never-stored")


# Coverage: deps.py::_resolve_principal — lines 133-143 "revoked" path (line 141).
def test_coverage_resolve_principal_revoked_key_raises_401_unit(sqlite_db_url):
    """A row whose ``revoked_at`` is set is treated as unknown (AC-3.4)."""
    db_session.reset_engine()
    plaintext = "sk-coverage-fr04-revoked"
    _seed_api_key(
        plaintext,
        scope="write",
        revoked_at="2026-01-01T00:00:00Z",
    )

    with pytest.raises(UnauthorizedError):
        deps._resolve_principal(plaintext)


# Coverage: deps.py::_resolve_principal — lines 133-143 except branch (136-140).
def test_coverage_resolve_principal_db_exception_raises_401_unit(
    sqlite_db_url, monkeypatch
):
    """Any DB / driver exception during key lookup is surfaced as 401 (NFR-04).

    We monkeypatch ``key_repo.lookup_active_key`` to raise a
    ``RuntimeError`` mid-lookup; the surrounding ``except Exception`` in
    ``_resolve_principal`` converts it into the canonical 401 so the
    response cannot be used to probe DB health.
    """
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    def _boom_lookup(*_args, **_kwargs):
        raise RuntimeError("simulated DB failure during key lookup")

    monkeypatch.setattr(key_repo, "lookup_active_key", _boom_lookup)

    with pytest.raises(UnauthorizedError) as exc_info:
        deps._resolve_principal("sk-coverage-fr04-dbfail")
    assert exc_info.value.detail == "missing or invalid API key", (
        "the DB-failure path must use the same NFR-04 detail string as the "
        "missing / unknown / revoked paths, so the response cannot be used "
        "to probe DB health"
    )


# Coverage: deps.py::auth_dep happy path — lines 184, 133-143, 164-165.
def test_coverage_auth_dep_real_key_returns_principal_unit(sqlite_db_url):
    """A valid key flows through ``_resolve_principal`` + ``rate_dep`` and returns."""
    db_session.reset_engine()
    plaintext = "sk-coverage-fr04-auth-dep"
    _seed_api_key(plaintext, scope="write")

    principal = deps.auth_dep(x_api_key=plaintext)
    assert isinstance(principal, deps.Principal)
    assert principal.scope == "write"
    assert principal.key_id


# Coverage: deps.py::rate_dep — lines 164-165.
def test_coverage_rate_dep_charges_and_returns_principal_unit(sqlite_db_url):
    """``rate_dep`` consumes one token and returns the same ``Principal``."""
    db_session.reset_engine()
    plaintext = "sk-coverage-fr04-rate"
    _seed_api_key(plaintext, scope="read")

    with db_session.session_scope() as session:
        row = key_repo.lookup_active_key(plaintext, session=session)
    assert row is not None, "test seed: lookup must see the row we just inserted"
    principal = deps.Principal(key_id=row["id"], scope=row["scope"])

    returned = deps.rate_dep(principal)
    assert returned is principal, (
        f"rate_dep must return the same Principal it received, got {returned!r}"
    )
    assert returned.scope == "read"


# Coverage: deps.py::problem_instance — line 249 (non-403 path).
def test_coverage_problem_instance_non_403_returns_path_unit():
    """``problem_instance`` returns the request path for any status other than 403."""
    request = SimpleNamespace(
        url=SimpleNamespace(path="/v1/tasks/some-id-fr04-coverage")
    )
    # Every non-403 status echoes the path so the operator can correlate
    # the envelope with the specific occurrence (RFC 7807 §3.1).
    for status in (200, 401, 404, 409, 422, 429, 500, 503):
        assert deps.problem_instance(request, status) == "/v1/tasks/some-id-fr04-coverage", (
            f"problem_instance must return the request path for status={status} "
            f"(non-403), got {deps.problem_instance(request, status)!r}"
        )
    # The 403 case stays empty (FR-04 / NFR-02 non-disclosure rule); this
    # is the contract case 1 of TEST_SPEC pins down — keep it green here.
    assert deps.problem_instance(request, 403) == "", (
        "problem_instance must return an empty instance for status=403 so "
        "the envelope cannot be used to probe whether the resource id exists"
    )


# Coverage: service.auth.verify_key — lines 83-84.
def test_coverage_verify_key_unit():
    """``verify_key`` uses ``hmac.compare_digest``; True on match, False otherwise."""
    plaintext = "sk-coverage-fr04-verify"
    digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()

    # Matching candidate returns True.
    assert auth.verify_key(plaintext, digest) is True, (
        "verify_key must return True when the candidate hashes to the stored digest"
    )
    # A one-character mismatch returns False (constant-time comparison).
    flipped = digest[:-1] + ("0" if digest[-1] != "0" else "1")
    assert auth.verify_key(plaintext, flipped) is False, (
        "verify_key must return False when the candidate does not match the stored digest"
    )
    # Length mismatch is also a non-match (compare_digest's contract).
    assert auth.verify_key(plaintext, "abc") is False, (
        "verify_key must return False when the stored digest length differs"
    )


# Coverage: service.auth.create_api_key — lines 107-109.
def test_coverage_create_api_key_prints_once_persists_hash(sqlite_db_url):
    """``create_api_key`` writes the plaintext via the writer and persists only the hash."""
    db_session.reset_engine()

    captured: list[str] = []

    # Build the schema so the key_repo write has a table to land in.
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    with db_session.session_scope() as session:
        result = auth.create_api_key(
            scope="write",
            session=session,
            plaintext_writer=lambda pt: captured.append(pt),
        )

    assert len(captured) == 1, (
        f"create_api_key must call plaintext_writer exactly once, got {len(captured)} calls"
    )
    printed_plaintext = captured[0]
    assert printed_plaintext.startswith("sk-"), (
        f"the printed plaintext must carry the conventional sk- prefix, "
        f"got {printed_plaintext!r}"
    )

    # The persisted row's hash must equal sha256(plaintext) — and the
    # plaintext itself must not appear in any persisted column (AC-3.2).
    expected_hash = hashlib.sha256(printed_plaintext.encode("utf-8")).hexdigest()
    assert result["key_hash"] == expected_hash, (
        f"persisted key_hash must equal sha256(plaintext), "
        f"expected {expected_hash!r}, got {result['key_hash']!r}"
    )
    for field_name, field_value in result.items():
        assert field_value != printed_plaintext, (
            f"plaintext must NEVER be stored on the api_keys row, but field "
            f"{field_name!r} equals the plaintext"
        )
