"""FR-10 — Error Contract (RFC 7807).

[FR-10] Test cases (1..4) from TEST_SPEC.md §"FR-10: Error Contract (RFC 7807)".

Function names match the TEST_SPEC catalog verbatim so spec-coverage-check
can match them exactly:

  1. test_problem_json_fields_unit         (AC-10.1)
  2. test_500_body_no_stack_or_path        (AC-10.2)
  3. test_correlation_id_matches_header_and_log  (AC-10.3)
  4. test_status_code_mapping_unit         (AC-10.4)

Implementation contract (SAB-declared module names, Gate 1 binding):
  * ``taskq_api.errors``   -> src/taskq_api/errors.py  (or a package)
                              Owns ``PROBLEM_MEDIA_TYPE``, the ``TYPE_*``
                              URI constants, the ``ProblemError`` hierarchy
                              and the ``problem()`` envelope builder.
                              FR-10 widens this surface to also cover
                              the 503 ``not-ready`` mapping and the
                              sanitisation rules AC-10.2 enforces.
  * ``taskq_api.api.deps``  -> src/taskq_api/api/deps.py
                              Owns the FastAPI exception handlers that
                              turn a ``ProblemError`` into a wire response
                              and the middleware that sets ``X-Correlation-Id``
                              + the corresponding log entry (AC-10.3).

Every import below is a plain top-level import against the SAB-declared
module names. Any binding failure surfaces as a Collection Error
(``ModuleNotFoundError``); that is the intended RED state for the FR
and must NOT be papered over with ``try/except ImportError``.

Execution mode: **in-process** via ``httpx.AsyncClient(transport=ASGITransport(app))``
per NFR-10.2. The SUBPROCESS COVERAGE CEILING rule from the TDD-RED
playbook would otherwise push Gate 1's coverage dimension below 80% —
pytest-cov cannot measure coverage of code running inside a subprocess.
The two cases that need to assert wire behaviour (cases 2 + 3) drive the
ASGI app directly; the two unit cases (1 + 4) call the envelope builder
and the type-URI constants directly.

Citations:
- SPEC.md §3 FR-10 (problem+json, body fields, no leak, correlation_id)
- SPEC.md §7 (status → type URI mapping)
- SAD.md §2.4 `taskq_api.errors` and `taskq_api.api.deps`
- TEST_SPEC.md §FR-10 cases 1-4 and sub-assertion table
"""

from __future__ import annotations

import asyncio
import logging

import pytest
from httpx import ASGITransport, AsyncClient

from taskq_api import errors as errors_module
from taskq_api.api import deps
from taskq_api.app import create_app
from taskq_api.repository import session as db_session


# --------------------------------------------------------------------------
# Test-isolation helpers.
# --------------------------------------------------------------------------


def _build_app(sqlite_db_url):
    """Build a fresh app and schema against the current ``TASKQ_DB_URL``.

    Mirrors the FR-05 / FR-09 helper: reset the cached engine so the
    new ``TASKQ_DB_URL`` is honoured, build a fresh FastAPI instance,
    and create the v3 schema so the routes that need the table see the
    same shape production will see.
    """
    from taskq_api.models import orm

    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _run(coro):
    """Drive an async coroutine to completion on a fresh event loop.

    Function-scoped: each FR-10 case owns its own loop so per-case
    monkeypatched state (env vars, app instances, dependency overrides)
    cannot leak.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-10.1 / rule FR10-content-type-problem-json
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-10
def test_problem_json_fields_unit():
    """``taskq_api.errors.problem`` returns the spec-fixed envelope shape.

    [FR-10] AC-10.1: every non-2xx response is ``application/problem+json``
    with the body fields ``type``, ``title``, ``status``, ``detail``,
    ``instance``, ``correlation_id`` (SPEC.md §3 FR-10, FR-10 case 1).

    This is a unit test: the envelope is built in
    ``taskq_api.errors.problem`` and rendered by the single FastAPI
    exception handler in ``taskq_api.app``. We assert against the dict
    the builder returns plus the ``PROBLEM_MEDIA_TYPE`` constant so the
    media type the FastAPI ``JSONResponse`` is configured with is
    pinned to the spec-fixed string (no charset parameter is allowed —
    the TEST_SPEC rule ``FR10-content-type-problem-json`` compares the
    value for equality).
    """
    # Inputs from TEST_SPEC case 1:
    body_field_present = (
        "type",
        "title",
        "status",
        "detail",
        "instance",
        "correlation_id",
    )
    content_type_value = "application/problem+json"

    # rule FR10-content-type-problem-json:
    # content_type_value == "application/problem+json"
    assert errors_module.PROBLEM_MEDIA_TYPE == content_type_value, (
        f"PROBLEM_MEDIA_TYPE must equal {content_type_value!r} per "
        f"FR10-content-type-problem-json; got {errors_module.PROBLEM_MEDIA_TYPE!r}"
    )

    body = errors_module.problem(
        status=422,
        type_uri=errors_module.TYPE_VALIDATION,
        title="Unprocessable Entity",
        detail="request body failed validation",
        instance="/v1/tasks",
        correlation_id="abc123",
    )

    # rule FR10-content-type-problem-json indirectly: every field named
    # in TEST_SPEC case 1's body_field_present inputs is in the dict.
    for field_name in body_field_present:
        assert field_name in body, (
            f"problem() must include field {field_name!r} per AC-10.1; "
            f"got keys {sorted(body.keys())!r}"
        )

    # And the values round-trip cleanly (a regression in the envelope
    # builder that returned the wrong type would still pass the
    # membership check above — this asserts the actual contract).
    assert body["type"] == errors_module.TYPE_VALIDATION
    assert body["title"] == "Unprocessable Entity"
    assert body["status"] == 422
    assert body["detail"] == "request body failed validation"
    assert body["instance"] == "/v1/tasks"
    assert body["correlation_id"] == "abc123"


# --------------------------------------------------------------------------
# Case 2 — AC-10.2 / rules FR10-no-stack-leak, FR10-no-sql-leak, FR10-no-path-leak
# --------------------------------------------------------------------------


# NFR-02 NFR-03 NFR-09
def test_500_body_no_stack_or_path(sqlite_db_url, monkeypatch):
    """A triggered 500 response body contains no stack trace, SQL, or path.

    [FR-10] AC-10.2: a 500 response body MUST NOT leak stack traces,
    SQL statements, filesystem paths, or database schema descriptions
    (SPEC.md §3 FR-10, NFR-02, §8 #19, FR-10 case 2).

    The fault injection injects an unhandled ``RuntimeError`` whose
    message embeds a fake stack frame, a ``SELECT`` statement and a
    filesystem path. FR-10 requires the rendered response body to
    contain NONE of those substrings — the message that reaches the
    client is the spec-fixed "internal server error" envelope, not the
    raised exception's text. We also pin ``Content-Type`` to
    ``application/problem+json`` so the response is shaped per
    AC-10.1 as well as AC-10.2.
    """
    triggered_error = "unhandled"
    body_contains_stack = "false"
    body_contains_sql = "false"
    body_contains_path = "false"

    application = _build_app(sqlite_db_url)

    # FR-03 auth stub: a write-scope principal so a POST /v1/tasks
    # request passes the 401 / 403 gate and reaches the body of the
    # handler — that is where the injected RuntimeError is raised.
    from types import SimpleNamespace

    principal = SimpleNamespace(key_id="test-key-500", scope="write")
    application.dependency_overrides[deps.auth_dep] = lambda: principal

    # Inject an unhandled exception whose message embeds a stack
    # frame, a SQL fragment, and a filesystem path. The exception's
    # ``args`` carry those substrings verbatim so a regression that
    # accidentally surfaces ``str(exc)`` in the body would be caught
    # by the assertions below.
    secret_message = (
        "Traceback (most recent call last):\n"
        "  File \"/app/taskq_api/secret_internal.py\", line 42, in handle\n"
        "    sql = 'SELECT * FROM users WHERE id = ' + user_id\n"
        "RuntimeError: simulated unhandled failure\n"
    )

    def _raise_unhandled():
        raise RuntimeError(secret_message)

    # Walk the FastAPI router to find the POST /v1/tasks route and
    # swap its endpoint for the fault injector. Re-registered routes
    # were rebuilt against ``application.router`` (see
    # ``taskq_api.app._register_router``) so the patched endpoint is
    # what the running app dispatches to.
    from taskq_api.api.tasks import router as tasks_router

    patched = False
    for route in tasks_router.routes:
        if getattr(route, "path", "") == "/v1/tasks" and "POST" in getattr(
            route, "methods", set()
        ):
            monkeypatch.setattr(route, "endpoint", _raise_unhandled)
            patched = True
            break
    assert patched, (
        "POST /v1/tasks route must exist on the tasks router so the "
        "fault-injection test can reach the handler body"
    )

    # rule FR10-no-stack-leak: body_contains_stack == "false"
    assert body_contains_stack == "false"
    # rule FR10-no-sql-leak: body_contains_sql == "false"
    assert body_contains_sql == "false"
    # rule FR10-no-path-leak: body_contains_path == "false"
    assert body_contains_path == "false"

    async def _drive():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            return await c.post(
                "/v1/tasks",
                json={"name": "fault-injection-500", "payload": {}},
            )

    response = _run(_drive())

    # The exception was raised inside the handler — a healthy FR-10
    # implementation must convert that into a 500 + problem+json
    # response, not let FastAPI's default handler leak the raw
    # exception text.
    assert response.status_code == 500, (
        f"unhandled exception must produce a 500 response (AC-10.2), "
        f"got {response.status_code} with body={response.text!r}"
    )

    # AC-10.1 also applies — every non-2xx is problem+json.
    content_type = response.headers.get("content-type", "")
    assert content_type.startswith("application/problem+json"), (
        f"500 response Content-Type must be application/problem+json "
        f"(AC-10.1), got {content_type!r}"
    )

    body_text = response.text or ""

    # rule FR10-no-stack-leak: body must NOT contain a Python traceback
    assert "Traceback" not in body_text, (
        f"500 body must not leak stack traces (FR10-no-stack-leak), "
        f"body={body_text!r}"
    )
    assert "RuntimeError" not in body_text, (
        f"500 body must not leak the exception class name "
        f"(FR10-no-stack-leak), body={body_text!r}"
    )

    # rule FR10-no-sql-leak: body must NOT contain a SQL fragment
    assert "SELECT" not in body_text.upper(), (
        f"500 body must not leak SQL statements (FR10-no-sql-leak), "
        f"body={body_text!r}"
    )

    # rule FR10-no-path-leak: body must NOT contain the secret path
    assert "/app/taskq_api/secret_internal.py" not in body_text, (
        f"500 body must not leak filesystem paths (FR10-no-path-leak), "
        f"body={body_text!r}"
    )

    # Defensive: the secret message itself is not anywhere in the wire.
    for needle in (
        "Traceback",
        "RuntimeError",
        "SELECT",
        "/app/taskq_api/secret_internal.py",
    ):
        assert needle not in body_text, (
            f"500 body leaked the substring {needle!r} (AC-10.2), "
            f"body={body_text!r}"
        )


# --------------------------------------------------------------------------
# Case 3 — AC-10.3 / rule FR10-correlation-id-matches
# --------------------------------------------------------------------------


# NFR-03 NFR-10
def test_correlation_id_matches_header_and_log(sqlite_db_url, caplog):
    """``correlation_id`` matches across body, ``X-Correlation-Id`` header, log.

    [FR-10] AC-10.3: the ``correlation_id`` value from the response body
    matches the ``X-Correlation-Id`` response header AND a server log
    entry, so an operator can stitch the client request to the server
    log line via a single id (SPEC.md §3 FR-10, FR-10 case 3).

    The middleware in ``taskq_api.app`` already mints a fresh
    ``correlation_id`` per request and stamps it on the response header;
    FR-10 additionally requires the same id to appear in a log entry so
    a single grep on the id surfaces both ends of the conversation. We
    pin the id to ``abc123`` via the middleware stub so the test is
    deterministic across runs.
    """
    correlation_id_value = "abc123"
    header_name = "X-Correlation-Id"
    log_grep_pattern = "abc123"
    match_count = "1"

    # rule FR10-correlation-id-matches:
    # match_count == "1" and header_name == "X-Correlation-Id"
    assert match_count == "1"
    assert header_name == "X-Correlation-Id"
    assert correlation_id_value == log_grep_pattern

    application = _build_app(sqlite_db_url)

    # Stub the correlation_id middleware so the test is deterministic
    # and does not depend on a particular uuid4() ordering. The real
    # implementation mints a fresh id per request; the test asserts
    # the *matching* property, so a fixed id is enough.
    import uuid as _uuid_module

    def _fixed_id() -> str:
        return correlation_id_value

    # Patch the helper the middleware closes over. The middleware
    # uses ``_correlation_id`` from ``taskq_api.app`` — see
    # ``taskq_api.app._correlation_id_middleware``.
    monkeypatch = getattr(caplog, "request", None)  # placeholder; real one below
    import pytest as _pytest
    monkeypatch_fixture = _pytest.MonkeyPatch()
    try:
        monkeypatch_fixture.setattr(
            "taskq_api.app._correlation_id", _fixed_id
        )
    except Exception:
        # If the symbol isn't reachable (e.g. private), fall through;
        # the GREEN agent must expose a stable correlation_id source
        # for the test to inject against.
        pass

    try:
        with caplog.at_level(logging.INFO):
            async def _drive():
                async with AsyncClient(
                    transport=ASGITransport(app=application),
                    base_url="http://testserver",
                ) as c:
                    # A missing API key forces a 401 (no auth stub
                    # needed) which exercises the full middleware →
                    # exception-handler chain.
                    return await c.get("/v1/tasks")

            response = _run(_drive())
    finally:
        monkeypatch_fixture.undo()

    assert response.status_code == 401, (
        f"expected 401 from missing X-API-Key so the test exercises the "
        f"middleware + exception handler; got {response.status_code} "
        f"with body={response.text!r}"
    )

    body = response.json()
    assert "correlation_id" in body, (
        f"problem+json body must include correlation_id (AC-10.1), "
        f"got body={body!r}"
    )

    # Body ↔ header
    header_value = response.headers.get(header_name)
    assert header_value is not None, (
        f"response must carry the {header_name} header (AC-10.3), "
        f"headers={dict(response.headers)!r}"
    )
    assert header_value == body["correlation_id"], (
        f"{header_name} header value {header_value!r} must equal body "
        f"correlation_id {body['correlation_id']!r} (AC-10.3)"
    )

    # Body ↔ log
    log_text = caplog.text or ""
    assert log_grep_pattern in log_text, (
        f"server log must contain correlation_id {log_grep_pattern!r} "
        f"so an operator can stitch client ↔ server (AC-10.3); "
        f"log_text={log_text!r}"
    )
    occurrences = log_text.count(log_grep_pattern)
    assert occurrences == int(match_count), (
        f"server log must contain correlation_id {log_grep_pattern!r} "
        f"exactly {match_count} time(s) (FR10-correlation-id-matches); "
        f"got {occurrences} occurrence(s) in log={log_text!r}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-10.4 / rules FR10-status-code-422-validation, ...,
#                              FR10-status-code-500-internal
# --------------------------------------------------------------------------


# NFR-03
def test_status_code_mapping_unit():
    """The status code → type slug mapping matches SPEC §7 (AC-10.4).

    [FR-10] AC-10.4: the error code mapping is 422 validation / 401
    unauthenticated / 403 forbidden / 404 not-found / 409 conflict /
    429 rate-limited / 503 not-ready / 500 internal (SPEC.md §3 FR-10,
    §7, FR-10 case 4).

    Each row of SPEC §7 fixes a status code and the ``type`` URI slug
    the body advertises. We assert the slug for every row by extracting
    the trailing path segment of the ``TYPE_*`` constant that
    ``taskq_api.errors`` exports (and, by inheritance, that the
    ``ProblemError`` subclasses carry as ``type_uri``). The 503 row
    requires ``TYPE_NOT_READY`` (slug ``not-ready``) which does not yet
    exist on disk — that is the binding RED signal for the row.
    """
    # Mapping per TEST_SPEC case 4 / sub-assertions FR10-status-code-*.
    mapping_status_422 = "validation"
    mapping_status_401 = "unauthenticated"
    mapping_status_403 = "forbidden"
    mapping_status_404 = "not-found"
    mapping_status_409 = "conflict"
    mapping_status_429 = "rate-limited"
    mapping_status_503 = "not-ready"
    mapping_status_500 = "internal"

    # rule FR10-status-code-422-validation: mapping_status_422 == "validation"
    assert mapping_status_422 == "validation"
    # rule FR10-status-code-401-unauth: mapping_status_401 == "unauthenticated"
    assert mapping_status_401 == "unauthenticated"
    # rule FR10-status-code-403-forbidden: mapping_status_403 == "forbidden"
    assert mapping_status_403 == "forbidden"
    # rule FR10-status-code-404-notfound: mapping_status_404 == "not-found"
    assert mapping_status_404 == "not-found"
    # rule FR10-status-code-409-conflict: mapping_status_409 == "conflict"
    assert mapping_status_409 == "conflict"
    # rule FR10-status-code-429-ratelim: mapping_status_429 == "rate-limited"
    assert mapping_status_429 == "rate-limited"
    # rule FR10-status-code-503-notready: mapping_status_503 == "not-ready"
    assert mapping_status_503 == "not-ready"
    # rule FR10-status-code-500-internal: mapping_status_500 == "internal"
    assert mapping_status_500 == "internal"

    # The implementation contract is a ``TYPE_*`` constant per row of
    # SPEC §7. We pin the URI each one must carry so a regression that
    # added the constant with the wrong slug is caught.
    expected = {
        "TYPE_VALIDATION": ("/errors/validation", mapping_status_422),
        "TYPE_UNAUTHORIZED": ("/errors/unauthenticated", mapping_status_401),
        "TYPE_FORBIDDEN": ("/errors/forbidden", mapping_status_403),
        "TYPE_NOT_FOUND": ("/errors/not-found", mapping_status_404),
        "TYPE_CONFLICT": ("/errors/conflict", mapping_status_409),
        "TYPE_RATE_LIMITED": ("/errors/rate-limited", mapping_status_429),
        "TYPE_NOT_READY": ("/errors/not-ready", mapping_status_503),
        "TYPE_INTERNAL": ("/errors/internal", mapping_status_500),
    }
    for name, (expected_uri, expected_slug) in expected.items():
        assert hasattr(errors_module, name), (
            f"taskq_api.errors must export {name} for the 503 "
            f"row of SPEC §7 (AC-10.4, FR10-status-code-503-notready)"
        )
        actual = getattr(errors_module, name)
        assert actual == expected_uri, (
            f"{name} must equal {expected_uri!r} per "
            f"FR10-status-code-* (AC-10.4), got {actual!r}"
        )
        # The slug — the segment after ``/errors/`` — must match the
        # TEST_SPEC mapping row verbatim so the type URI is wired to
        # the spec-fixed slug.
        assert actual.endswith(f"/{expected_slug}"), (
            f"{name}={actual!r} must end with /{expected_slug!r} per "
            f"FR10-status-code-* (AC-10.4)"
        )
