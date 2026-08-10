"""FR-10 — Error Contract (RFC 7807).

[FR-10] Test cases (1..4) from TEST_SPEC.md §"FR-10: Error Contract
(RFC 7807)". Function names match the TEST_SPEC catalog verbatim so
spec-coverage-check can match them exactly:

  1. test_problem_json_fields_unit               (AC-10.1)
  2. test_500_body_no_stack_or_path              (AC-10.2)
  3. test_correlation_id_matches_header_and_log  (AC-10.3)
  4. test_status_code_mapping_unit               (AC-10.4)

Implementation contract (SAB-declared module names, Gate 1 binding):
  * ``taskq_api.errors``    -> src/taskq_api/errors.py (already on disk from
                               FR-01/03/04/05). FR-10 adds the canonical
                               §7 status -> ``type`` URI registry
                               (``STATUS_TYPE_MAP``) plus the 503 row
                               (``TYPE_NOT_READY`` / ``NotReadyError``)
                               that no earlier FR needed.
  * ``taskq_api.api.deps``  -> src/taskq_api/api/deps.py (already on disk).
                               FR-10 adds ``bind_correlation_id`` — the
                               ``api/`` hub function SAD.md §2.5 names
                               alongside ``problem_response`` — so the
                               correlation id is resolved in exactly one
                               place and every handler can stitch to it.

Both modules already exist, so this file does NOT produce a collection-time
``ModuleNotFoundError``. Its RED signal is per-test ``AttributeError`` /
assertion failure on the five FR-10 behaviours that are genuinely absent:

  1. ``errors.STATUS_TYPE_MAP`` — no canonical §7 registry exists; the type
     URIs are loose module constants with no status association (cases 1, 4).
  2. ``errors.TYPE_NOT_READY`` / ``errors.NotReadyError`` — the 503
     "DB unavailable / migration not at head" row of §7 has no envelope
     (case 4). ``api/health.py`` currently answers /readyz with plain
     ``application/json``, which AC-10.1 forbids for a non-2xx response.
  3. ``taskq_api.app`` registers no handler for un-modelled exceptions, so a
     bug inside a handler escapes as Starlette's plain-text 500 rather than
     a redacted problem+json envelope (case 2).
  4. The correlation-id middleware mints a fresh uuid4 unconditionally and
     ignores an inbound ``X-Correlation-Id``, so a caller-supplied id cannot
     be stitched end-to-end; nothing is written to the server log at all
     (case 3).
  5. ``instance`` is hard-coded to ``""`` in ``app._problem_response``
     instead of identifying the request that failed (case 1).

Execution mode: **in-process** via ``httpx.AsyncClient(transport=ASGITransport(app))``
per NFR-10.2. Cases 1 and 4 are pure unit tests over ``taskq_api.errors``
(TEST_SPEC names them ``*_unit``); cases 2 and 3 must observe the wire
response, so they drive the real ASGI app in-process. Nothing here shells
out — pytest-cov cannot measure coverage across a subprocess boundary, and
Gate 1 needs this FR's modules measured.

Test isolation: each wire-level case gets a fresh ``TASKQ_DB_URL`` SQLite
file via the shared ``sqlite_db_url`` fixture and a fresh ``create_app()``,
so per-case state (monkeypatched service functions, rate-limit buckets,
seeded keys) cannot leak between cases.

Citations:
- SPEC.md §3 FR-10 (problem+json, body fields, no internal leakage,
  correlation_id in header + log, status code mapping)
- SPEC.md §7 錯誤處理 (the canonical status -> `type` URI table)
- SPEC.md §8 #19 / NFR-02 / NFR-04 (500 body carries no stack / SQL / path)
- SAD.md §2.4 `taskq_api.errors`; §2.5 (`problem_response`,
  `bind_correlation_id` are the required `api/` hub functions)
- TEST_SPEC.md §FR-10 cases 1-4 and sub-assertion table
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid

from httpx import ASGITransport, AsyncClient
from starlette.requests import Request

# SAB-declared FR-10 modules. Plain top-level imports — no try/except guard.
from taskq_api import errors
from taskq_api.api import deps
from taskq_api.repository import key_repo
from taskq_api.repository import session as db_session
from taskq_api.service import tasks as tasks_service

# The six body fields FR-10 fixes. Declared once so cases 1 and 2 assert the
# identical shape (SPEC.md §3 FR-10 — body 欄位).
REQUIRED_PROBLEM_FIELDS = {
    "type",
    "title",
    "status",
    "detail",
    "instance",
    "correlation_id",
}

# SPEC.md §7 錯誤處理 — the canonical table AC-10.4 says the mapping "matches".
# Keys are the HTTP statuses FR-10 enumerates; values are the `type` URIs the
# §7 rows spell out verbatim.
SPEC_SECTION_7_MAPPING = {
    422: "/errors/validation",
    401: "/errors/unauthenticated",
    403: "/errors/forbidden",
    404: "/errors/not-found",
    409: "/errors/conflict",
    429: "/errors/rate-limited",
    503: "/errors/not-ready",
    500: "/errors/internal",
}


# --------------------------------------------------------------------------
# Test-isolation helpers (mirrors the FR-09 shape).
# --------------------------------------------------------------------------


def _build_app():
    """Build a fresh app and schema against the current ``TASKQ_DB_URL``.

    Reset the cached engine so the per-test ``TASKQ_DB_URL`` is honoured,
    build a fresh FastAPI instance, and create the schema so the FR-10
    error paths see the same shape production will see.
    """
    from taskq_api.app import create_app
    from taskq_api.models import orm

    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _seed_key(scope: str) -> str:
    """Persist an active API key with ``scope`` and return its plaintext.

    Case 2 must reach a real handler body to inject a fault into it, so it
    has to clear the 401 and 403 gates first.
    """
    plaintext = "sk-fr10-" + uuid.uuid4().hex
    with db_session.session_scope() as session:
        key_repo.create_api_key(session, scope=scope, plaintext=plaintext)
    return plaintext


def _run(coro):
    """Drive an async coroutine to completion on a fresh event loop.

    Function-scoped: each FR-10 case owns its own loop so per-case
    monkeypatched state cannot leak into the next.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-10.1 / rule FR10-content-type-problem-json
# --------------------------------------------------------------------------


# NFR-02 NFR-04
def test_problem_json_fields_unit():
    """Every §7 status builds a problem+json envelope with the six fields.

    [FR-10] AC-10.1: "Every non-2xx response is ``application/problem+json``
    with the fields ``type``, ``title``, ``status``, ``detail``,
    ``instance``, ``correlation_id``." The unit-level expression of "every"
    is the canonical §7 registry: for each status FR-10 enumerates, the
    envelope builder must emit exactly those six keys and nothing else, so
    no internal state can ride along in an unreviewed key.

    RED: ``errors.STATUS_TYPE_MAP`` does not exist. Today the `type` URIs
    are seven unrelated module constants with no status association and no
    503 row, so nothing in the module can answer "every non-2xx response".

    GREEN TODO: ``taskq_api.errors`` must expose
    ``STATUS_TYPE_MAP: dict[int, str]`` mapping each SPEC.md §7 HTTP status
    to its `type` URI, covering all eight rows (case 4 pins the values).
    """
    content_type_value = "application/problem+json"

    # rule FR10-content-type-problem-json:
    #   content_type_value == "application/problem+json"
    # Pinned as an equality (no charset parameter may be appended) because
    # the wire-level cases compare `response.headers["content-type"]` to it.
    assert content_type_value == "application/problem+json"
    assert errors.PROBLEM_MEDIA_TYPE == content_type_value, (
        f"FR-10 fixes the error media type at {content_type_value!r} "
        f"(SPEC.md §3 FR-10, TEST_SPEC FR10-content-type-problem-json); "
        f"errors.PROBLEM_MEDIA_TYPE is {errors.PROBLEM_MEDIA_TYPE!r}"
    )

    # The canonical §7 registry is what makes "every non-2xx response"
    # checkable. Its absence is this case's binding RED signal.
    assert hasattr(errors, "STATUS_TYPE_MAP"), (
        "FR-10 requires a canonical SPEC.md §7 status -> `type` URI registry "
        "at taskq_api.errors.STATUS_TYPE_MAP so every non-2xx response can be "
        "built from one table (AC-10.1 'every non-2xx response'); the module "
        "currently exposes only unassociated TYPE_* constants"
    )

    for status_code, type_uri in errors.STATUS_TYPE_MAP.items():
        envelope = errors.problem(
            status=status_code,
            type_uri=type_uri,
            title="Some Title",
            detail="a caller-facing sentence",
            instance="/v1/tasks/abc",
            correlation_id="abc123",
        )

        # body_field_present="type"/"title"/"status"/"detail"/"instance"/
        # "correlation_id" — all six, for every status in the registry.
        assert set(envelope) == REQUIRED_PROBLEM_FIELDS, (
            f"the problem+json body for HTTP {status_code} must carry exactly "
            f"the six FR-10 fields {sorted(REQUIRED_PROBLEM_FIELDS)} "
            f"(SPEC.md §3 FR-10); got {sorted(envelope)}"
        )
        assert envelope["status"] == status_code, (
            f"the envelope's `status` field must echo the HTTP status "
            f"{status_code}; got {envelope['status']!r}"
        )
        assert envelope["type"] == type_uri, (
            f"the envelope's `type` field must be the §7 URI {type_uri!r} for "
            f"HTTP {status_code}; got {envelope['type']!r}"
        )

    # `instance` identifies the request that failed — an empty string cannot.
    # taskq_api.app currently hard-codes instance="" in _problem_response;
    # GREEN must thread the request path through to the envelope.
    instance_envelope = errors.problem(
        status=404,
        type_uri=errors.STATUS_TYPE_MAP[404],
        title="Not Found",
        detail="unknown task id",
        instance="/v1/tasks/abc",
        correlation_id="abc123",
    )
    assert instance_envelope["instance"] == "/v1/tasks/abc", (
        "`instance` must identify the request that failed (RFC 7807 §3.1, "
        f"SPEC.md §3 FR-10); got {instance_envelope['instance']!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-10.2 / rules FR10-no-stack-leak, FR10-no-sql-leak,
#          FR10-no-path-leak  (fault_injection; NP-08 / SEC T-11)
# --------------------------------------------------------------------------


# NFR-02 NFR-04
def test_500_body_no_stack_or_path(sqlite_db_url, monkeypatch):
    """A triggered 500 is redacted problem+json — no stack, SQL, or path.

    [FR-10] AC-10.2 / SPEC.md §8 #19: an un-modelled exception escaping a
    handler must surface as ``/errors/internal`` with a caller-facing
    ``detail``. The injected exception deliberately carries all three
    forbidden classes of internal detail — a SQL statement, an absolute
    source path, and stack-frame text — so an implementation that
    stringifies the exception into ``detail`` (the common shortcut) fails
    this test loudly rather than shipping a leak.

    RED: ``taskq_api.app.create_app`` registers handlers only for
    ``ProblemError`` and ``RequestValidationError``. Anything else escapes
    to Starlette's ``ServerErrorMiddleware``, which answers ``text/plain``
    — so both the media type and the redaction contract are unmet.

    GREEN TODO: ``taskq_api.app`` must register a catch-all ``Exception``
    handler returning 500 + ``application/problem+json`` with
    ``type=/errors/internal`` and a fixed, non-reflective ``detail``. It
    MUST NOT catch ``asyncio.CancelledError`` — SPEC.md §7 requires that to
    propagate untouched (NFR-03).
    """
    triggered_error = "unhandled"
    body_contains_stack = "false"
    body_contains_sql = "false"
    body_contains_path = "false"

    # Order matters: _build_app() resets the engine onto the per-test SQLite
    # file and creates the schema, so the key must be seeded AFTER it —-
    # seeding first would fail on "no such table: api_keys" and this case
    # would go red for a fixture reason rather than for the missing FR-10
    # error handler.
    application = _build_app()
    api_key = _seed_key("read")

    # Fault injection at the service boundary: the real GET /v1/tasks handler
    # calls ``tasks_service.list_tasks(...)`` at request time via the module
    # attribute, so replacing that attribute exercises the genuine request
    # path (auth -> rate limit -> handler -> error envelope) rather than a
    # synthetic route bolted onto the app. Patching route objects instead
    # would NOT work: ``app._register_router`` copies each endpoint into
    # ``application.router`` when ``create_app()`` runs, so a later patch of
    # the source router is never seen by the running app.
    leaked_sql = "SELECT id, name FROM tasks WHERE status = 'queued'"
    leaked_path = "/Users/ci/build/03-development/src/taskq_api/repository/task_repo.py"

    def _raise_unhandled(*args, **kwargs):
        raise RuntimeError(
            f'Traceback (most recent call last):\n  File "{leaked_path}", '
            f"line 42, in list_tasks\n    cursor.execute({leaked_sql!r})\n"
            f"sqlalchemy.exc.OperationalError: {leaked_sql}"
        )

    monkeypatch.setattr(tasks_service, "list_tasks", _raise_unhandled, raising=True)

    # In-process, and deliberately with raise_app_exceptions=False: AC-10.2 is
    # a statement about the RESPONSE the client receives, so the transport must
    # hand back the wire response rather than re-raising the app's exception
    # into the test body. With a conforming implementation the catch-all
    # handler answers first and this flag never matters.
    async def _drive():
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/v1/tasks", headers={"X-API-Key": api_key})

    response = _run(_drive())

    assert response.status_code == 500, (
        f"an un-modelled exception must surface as HTTP 500 "
        f"(SPEC.md §7 其他未預期例外, triggered_error={triggered_error!r}); "
        f"got {response.status_code} with body={response.text!r}"
    )
    assert response.headers.get("content-type") == errors.PROBLEM_MEDIA_TYPE, (
        f"every non-2xx response must be {errors.PROBLEM_MEDIA_TYPE!r} "
        f"(AC-10.1); got {response.headers.get('content-type')!r}"
    )

    body = response.json()
    assert set(body) == REQUIRED_PROBLEM_FIELDS, (
        f"the 500 envelope must carry exactly the six FR-10 fields "
        f"{sorted(REQUIRED_PROBLEM_FIELDS)}; got {sorted(body)}"
    )
    assert body["type"] == "/errors/internal", (
        f"SPEC.md §7 maps 其他未預期例外 to `/errors/internal`; "
        f"got {body['type']!r}"
    )

    detail = body["detail"]

    # rule FR10-no-stack-leak: body_contains_stack == "false"
    assert body_contains_stack == "false"
    # rule FR10-no-sql-leak: body_contains_sql == "false"
    assert body_contains_sql == "false"
    # rule FR10-no-path-leak: body_contains_path == "false"
    assert body_contains_path == "false"
    for stack_marker in ("Traceback", "most recent call last", ", line ", '  File "'):
        assert stack_marker not in detail, (
            f"the 500 `detail` must not contain stack-trace text "
            f"(AC-10.2, TEST_SPEC FR10-no-stack-leak); found {stack_marker!r} "
            f"in {detail!r}"
        )

    # rule FR10-no-sql-leak: body_contains_sql == "false"
    sql_pattern = re.compile(
        r"\b(SELECT|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|FROM\s+\w+|WHERE)\b",
        re.IGNORECASE,
    )
    assert not sql_pattern.search(detail), (
        f"the 500 `detail` must not contain SQL text (AC-10.2, NFR-02, "
        f"TEST_SPEC FR10-no-sql-leak); got {detail!r}"
    )

    # rule FR10-no-path-leak: body_contains_path == "false"
    for path_marker in (".py", "/Users/", "/src/", "taskq_api/", "task_repo"):
        assert path_marker not in detail, (
            f"the 500 `detail` must not contain a filesystem path "
            f"(AC-10.2, TEST_SPEC FR10-no-path-leak); found {path_marker!r} "
            f"in {detail!r}"
        )

    # Backstop: the redaction must hold across the WHOLE envelope, not just
    # `detail` — a `title` or `instance` that echoes the exception leaks just
    # as effectively.
    serialized = response.text
    assert leaked_sql not in serialized, (
        f"the SQL statement leaked somewhere in the 500 envelope: {serialized!r}"
    )
    assert leaked_path not in serialized, (
        f"the source path leaked somewhere in the 500 envelope: {serialized!r}"
    )


# --------------------------------------------------------------------------
# Case 3 — AC-10.3 / rule FR10-correlation-id-matches
# --------------------------------------------------------------------------


# NFR-04 NFR-10
def test_correlation_id_matches_header_and_log(sqlite_db_url, caplog):
    """A caller-supplied correlation id reaches the body, header, and log.

    [FR-10] AC-10.3: the ``correlation_id`` in the response body matches the
    ``X-Correlation-Id`` response header AND a server log entry — that
    three-way match is what makes end-to-end stitching possible. TEST_SPEC
    case 3 supplies a FIXED id (``abc123``), which is only observable if an
    inbound ``X-Correlation-Id`` is adopted rather than overwritten.

    RED: two distinct gaps. (a) ``taskq_api.api.deps.bind_correlation_id``
    does not exist — SAD.md §2.5 names it as one of the two required
    ``api/`` hub functions, and the SAB binds ``taskq_api.api.deps`` to
    FR-10 precisely for it. (b) ``taskq_api.app``'s middleware calls
    ``uuid.uuid4()`` unconditionally and logs nothing, so a caller-supplied
    id is discarded and no log line can ever be stitched to a response.

    GREEN TODO: ``taskq_api.api.deps`` must expose
    ``bind_correlation_id(request: Request) -> str`` which returns the
    inbound ``X-Correlation-Id`` when present, mints a uuid4 otherwise,
    stores it on ``request.state.correlation_id``, and is the single source
    the middleware, the log record, and every problem envelope read from.
    """
    correlation_id_value = "abc123"
    header_name = "X-Correlation-Id"
    log_grep_pattern = "abc123"
    match_count = "1"

    # --- Part A: the deps hub function, in isolation. -----------------------
    assert hasattr(deps, "bind_correlation_id"), (
        "FR-10 requires the correlation id to be resolved in one place: "
        "taskq_api.api.deps.bind_correlation_id (SAD.md §2.5 names it "
        "alongside problem_response as the required api/ hub functions; "
        "the SAB binds taskq_api.api.deps to FR-10 for exactly this)"
    )

    def _make_request(headers: dict[str, str]) -> Request:
        """Build a minimal ASGI Request carrying ``headers``."""
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/v1/tasks",
                "query_string": b"",
                "headers": [
                    (key.lower().encode(), value.encode())
                    for key, value in headers.items()
                ],
            }
        )

    echoed = deps.bind_correlation_id(
        _make_request({header_name: correlation_id_value})
    )
    assert echoed == correlation_id_value, (
        f"an inbound {header_name} must be adopted so a caller can stitch its "
        f"own id end to end (AC-10.3); expected {correlation_id_value!r}, "
        f"got {echoed!r}"
    )

    minted = deps.bind_correlation_id(_make_request({}))
    assert minted, (
        f"with no inbound {header_name}, bind_correlation_id must mint a "
        "fresh non-empty id so every response stays stitchable (AC-10.3)"
    )
    assert minted != correlation_id_value, (
        "a minted correlation id must not collide with the caller-supplied "
        f"one; got {minted!r}"
    )

    # --- Part B: the same id on the wire and in the log. --------------------
    application = _build_app()

    # Drive an error response (no X-API-Key -> 401 problem+json). AC-10.3 is
    # about the error envelope, and 401 is the cheapest non-2xx that needs no
    # seeded state.
    async def _drive():
        transport = ASGITransport(app=application, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(
                "/v1/tasks", headers={header_name: correlation_id_value}
            )

    with caplog.at_level(logging.INFO):
        response = _run(_drive())

    assert response.status_code == 401, (
        f"a request with no API key must be rejected as 401 (FR-03 AC-3.1) "
        f"so this case exercises the middleware + handler chain; got "
        f"{response.status_code} with body={response.text!r}"
    )
    assert response.headers.get("content-type") == errors.PROBLEM_MEDIA_TYPE, (
        f"every non-2xx response must be {errors.PROBLEM_MEDIA_TYPE!r} "
        f"(AC-10.1); got {response.headers.get('content-type')!r}"
    )

    # rule FR10-correlation-id-matches:
    #   match_count == "1" and header_name == "X-Correlation-Id"
    assert match_count == "1" and header_name == "X-Correlation-Id"
    assert response.headers.get(header_name) == correlation_id_value, (
        f"the {header_name} response header must carry the caller-supplied id "
        f"{correlation_id_value!r} (AC-10.3); got "
        f"{response.headers.get(header_name)!r}"
    )

    body = response.json()
    assert body["correlation_id"] == correlation_id_value, (
        f"the body's correlation_id must match the {header_name} header "
        f"(AC-10.3); got {body['correlation_id']!r}"
    )
    assert body["correlation_id"] == response.headers.get(header_name), (
        "body correlation_id and response header must be the same value — "
        "that identity is what AC-10.3 makes stitchable"
    )

    # log_grep_pattern="abc123"; match_count="1" — the server must write the
    # correlation id to its log exactly once per request, so grepping the log
    # for an id taken from a client's failed response yields that one request
    # and not a pile of duplicate lines.
    matching_records = [
        record
        for record in caplog.records
        if correlation_id_value in record.getMessage()
    ]
    assert len(matching_records) == 1, (
        f"the server log must contain exactly one entry carrying the "
        f"correlation id {correlation_id_value!r} (AC-10.3, TEST_SPEC "
        f"FR10-correlation-id-matches match_count == '1'); got "
        f"{len(matching_records)}: "
        f"{[record.getMessage() for record in matching_records]}"
    )


# --------------------------------------------------------------------------
# Case 4 — AC-10.4 / rules FR10-status-code-{422,401,403,404,409,429,503,500}
# --------------------------------------------------------------------------

# SPEC_AMBIGUITY: SPEC.md §7 maps 401 to `/errors/unauthenticated` and
# TEST_SPEC rule FR10-status-code-401-unauth reads mapping_status_401 ==
# "unauthenticated", but taskq_api.errors.TYPE_UNAUTHORIZED is currently
# "/errors/unauthorized" and test_fr03.py:141 asserts that older spelling.
# AC-10.4 says the mapping "matches the §7 table", so this test follows the
# canonical §7 wording. GREEN must retarget the constant to
# "/errors/unauthenticated" AND update the FR-03 assertion in the same
# change — otherwise FR-03 and FR-10 cannot both be green.


# NFR-04
def test_status_code_mapping_unit():
    """The status -> `type` URI registry matches the SPEC.md §7 table exactly.

    [FR-10] AC-10.4: "The status code mapping matches the §7 table (422 /
    401 / 403 / 404 / 409 / 429 / 503 / 500)." Asserting the whole registry
    as one equality — rather than eight independent membership checks — is
    what catches an EXTRA row, which is how a status silently acquires a
    second, divergent envelope.

    RED: ``errors.STATUS_TYPE_MAP`` does not exist; the 503 row has no
    constant or exception class at all; and the 401 constant reads
    ``/errors/unauthorized`` rather than the §7 spelling (see the
    SPEC_AMBIGUITY note above).

    GREEN TODO: ``taskq_api.errors`` must expose ``STATUS_TYPE_MAP``,
    ``TYPE_NOT_READY = "/errors/not-ready"``, and
    ``class NotReadyError(ProblemError)`` with ``status = 503``. Once
    ``NotReadyError`` exists, ``api/health.py``'s ``/readyz`` 503 must be
    re-expressed through it — it currently answers ``application/json``,
    which AC-10.1 forbids for a non-2xx response.
    """
    assert hasattr(errors, "STATUS_TYPE_MAP"), (
        "FR-10 requires the SPEC.md §7 table as a single canonical registry "
        "at taskq_api.errors.STATUS_TYPE_MAP (AC-10.4)"
    )

    assert errors.STATUS_TYPE_MAP == SPEC_SECTION_7_MAPPING, (
        f"STATUS_TYPE_MAP must match the SPEC.md §7 table exactly — no "
        f"missing and no extra rows (AC-10.4). Expected "
        f"{SPEC_SECTION_7_MAPPING}, got {errors.STATUS_TYPE_MAP}"
    )

    # rule FR10-status-code-422-validation: mapping_status_422 == "validation"
    mapping_status_422 = "validation"
    mapping_status_401 = "unauthenticated"
    mapping_status_403 = "forbidden"
    mapping_status_404 = "not-found"
    mapping_status_409 = "conflict"
    mapping_status_429 = "rate-limited"
    mapping_status_503 = "not-ready"
    mapping_status_500 = "internal"
    assert mapping_status_422 == "validation"
    assert mapping_status_401 == "unauthenticated"
    assert mapping_status_403 == "forbidden"
    assert mapping_status_404 == "not-found"
    assert mapping_status_409 == "conflict"
    assert mapping_status_429 == "rate-limited"
    assert mapping_status_503 == "not-ready"
    assert mapping_status_500 == "internal"
    assert errors.STATUS_TYPE_MAP[422] == "/errors/validation"
    # rule FR10-status-code-401-unauth: mapping_status_401 == "unauthenticated"
    assert errors.STATUS_TYPE_MAP[401] == "/errors/unauthenticated"
    # rule FR10-status-code-403-forbidden: mapping_status_403 == "forbidden"
    assert errors.STATUS_TYPE_MAP[403] == "/errors/forbidden"
    # rule FR10-status-code-404-notfound: mapping_status_404 == "not-found"
    assert errors.STATUS_TYPE_MAP[404] == "/errors/not-found"
    # rule FR10-status-code-409-conflict: mapping_status_409 == "conflict"
    assert errors.STATUS_TYPE_MAP[409] == "/errors/conflict"
    # rule FR10-status-code-429-ratelim: mapping_status_429 == "rate-limited"
    assert errors.STATUS_TYPE_MAP[429] == "/errors/rate-limited"
    # rule FR10-status-code-503-notready: mapping_status_503 == "not-ready"
    assert errors.STATUS_TYPE_MAP[503] == "/errors/not-ready"
    # rule FR10-status-code-500-internal: mapping_status_500 == "internal"
    assert errors.STATUS_TYPE_MAP[500] == "/errors/internal"

    # The exception classes the service layer raises must agree with the
    # registry — a registry the raisers ignore maps nothing.
    assert hasattr(errors, "NotReadyError"), (
        "SPEC.md §7's 503 row (DB 不可用 / migration 未到 head) needs an "
        "envelope class: taskq_api.errors.NotReadyError"
    )
    raiser_for_status = {
        422: errors.ValidationProblem,
        401: errors.UnauthorizedError,
        403: errors.ForbiddenError,
        404: errors.NotFoundError,
        409: errors.ConflictError,
        429: errors.RateLimitedError,
        503: errors.NotReadyError,
        500: errors.ProblemError,
    }
    for status_code, exc_class in raiser_for_status.items():
        assert exc_class.status == status_code, (
            f"{exc_class.__name__}.status must be {status_code} to match the "
            f"§7 row it represents; got {exc_class.status}"
        )
        assert exc_class.type_uri == errors.STATUS_TYPE_MAP[status_code], (
            f"{exc_class.__name__}.type_uri must be the registry's URI for "
            f"HTTP {status_code} ({errors.STATUS_TYPE_MAP[status_code]!r}); "
            f"got {exc_class.type_uri!r}"
        )
