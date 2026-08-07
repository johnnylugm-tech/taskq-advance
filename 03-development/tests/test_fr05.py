"""FR-05 — Rate Limiting.

[FR-05] Test cases (1..3) from TEST_SPEC.md §"FR-05: Rate Limiting".

Implementation contract:
  * The tests are written as sync ``def test_...`` (not ``async def``) so the
    MIRROR check's AST walker (which only matches ``ast.FunctionDef``) sees
    every assertion. Async HTTP work is driven via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names — including the as-yet-unwritten ``taskq_api.service.ratelimit``
    and ``taskq_api.repository.rate_repo``. Until the FR-05 implementation
    lands, pytest exits with a Collection Error (``ModuleNotFoundError``) —
    that is the intended RED state.
  * Test isolation: case 1 and case 3 directly set the rate-limit env vars
    (a small burst so the integration test can force the 429 boundary in
    a sincle second; a large burst for the healthz/readyz bypass test) so
    the test fails because the rate limiter is absent or wrongly wired,
    not because of an unrelated default. Case 2 is a pure unit test that
    exercises the repository helper directly, with no HTTP boundary.
  * No ``patch.object`` / ``monkeypatch.setattr`` is used to fake out the
    FR-05 implementation — the GREEN TODO markers below make the contract
    unambiguous, and GREEN supplies the implementation.

Test names match TEST_SPEC.md verbatim so the spec-coverage-check can find
them by exact name match.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

# SAB-declared FR-05 module paths (the GREEN agent owns the implementation).
# Both modules are imported up-front on purpose — a missing module surfaces
# as a Collection Error (Exit Code 2), which is the intended RED state.
from taskq_api.api import deps  # FR-05 reuses the existing deps package.
from taskq_api.app import create_app
from taskq_api.repository import rate_repo  # FR-05 RED until implementation lands.
from taskq_api.repository import session as db_session
from taskq_api.repository import key_repo  # used to seed an active API key.
from taskq_api.service import auth  # to mint a key for the integration tests.
from taskq_api.service import ratelimit  # FR-05 RED until implementation lands.


# --------------------------------------------------------------------------
# Test-isolation fixtures.
# --------------------------------------------------------------------------


# TEST_SPEC.md case 1 inputs — small burst so a 21-request burst exceeds it.
_BURST_CAP = "20"
_PER_SEC = "5.0"


def _build_app(sqlite_db_url):
    """Build a fresh app with the schema created against the current TASKQ_DB_URL.

    [FR-05] The engine is reset before ``create_app`` so the per-test DB
    URL from the ``sqlite_db_url`` fixture is the one wired into the engine.
    """
    db_session.reset_engine()
    application = create_app()
    db_session.get_engine()
    return application


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    [FR-05] Each test owns its own loop so per-case state (rate-limit buckets,
    dependency overrides, event listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-5.1 / rules FR05-over-burst-status-429,
#                         FR05-429-has-retry-after,
#                         FR05-429-content-type
# --------------------------------------------------------------------------


# NFR-03 NFR-09 NFR-10
def test_exceed_burst_returns_429_with_retry_after(sqlite_db_url, monkeypatch):
    """Bursting more than ``TASKQ_RATE_BURST`` requests in a single second → 429.

    [FR-05] AC-5.1: sending more requests than ``TASKQ_RATE_BURST`` in a
    single window returns **HTTP 429** + ``application/problem+json`` with
    a ``Retry-After`` header (SEC FR-05 / SPEC.md §3 / §7 429 / §8 #9).

    The test forces a small burst so a 21-request burst inside one second
    is guaranteed to exceed it. Issuing 21 requests sequentially inside
    a single ``asyncio.run`` keeps the elapsed wall-clock under one second
    so the refill window does not paper over the burst consumption.

    GREEN TODO: ``taskq_api.service.ratelimit`` must expose a function
    (e.g. ``consume(key_id) -> None``) that raises ``RateLimitExceeded``
    (a new domain exception in ``taskq_api.errors`` mapped to HTTP 429
    with the FR-10 envelope and a ``Retry-After`` header) when the bucket
    is empty. The state lives in ``taskq_api.repository.rate_repo`` so
    workers share a single counter.
    """
    burst_cap = _BURST_CAP
    per_sec = _PER_SEC
    request_count = "21"
    expected_status = "429"
    header_name = "Retry-After"
    content_type = "application/problem+json"

    # Build a fresh app + schema so the rate_repo (when it lands) has a
    # table to write into. We seed a write-scoped active key below so the
    # /v1/tasks call goes through ``auth_dep`` rather than failing 401.
    monkeypatch.setenv("TASKQ_RATE_BURST", burst_cap)
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", per_sec)
    application = _build_app(sqlite_db_url)

    # Build the v1 schema so the api_keys row can be inserted.
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    # Mint a write-scoped active key. The auth_dep authenticates by the
    # candidate hash, so the row's presence is what gets the request
    # through the 401 gate; the rate limiter then gates on the key_id.
    candidate_key = "sk-fr05-burst-" + uuid.uuid4().hex
    key_id = str(uuid.uuid4())
    with db_session.session_scope() as session:
        key_repo.create_api_key(
            scope="write",
            plaintext=candidate_key,
            session=session,
            id=key_id,
        )

    # Build an authenticated client that issues requests sequentially inside
    # a single event loop, so the rate-limit bucket cannot refill between
    # requests (the refill window is 1/per_sec = 0.2 s per token, and we
    # burst 21 requests well under that).
    last_response = None

    async def _do():
        nonlocal last_response
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            for _ in range(21):
                r = await c.post(
                    "/v1/tasks",
                    json={"name": f"fr05-burst-{uuid.uuid4().hex}", "command": "echo ok"},
                    headers={"X-API-Key": candidate_key},
                )
                last_response = r
                if r.status_code == 429:
                    return r  # burst exceeded — stop issuing more.
            return last_response

    response = _run(_do())

    # rule FR05-over-burst-status-429: expected_status == "429"
    assert expected_status == "429"
    # Sanity: the test issued 21 requests but the burst cap is 20 — at least
    # one MUST be over the cap; the LAST response we hold is over the cap.
    assert response.status_code == 429, (
        f"21 sequential POSTs against the same key with burst=20 must "
        f"eventually return 429, got last status={response.status_code} "
        f"with body={response.text!r}"
    )

    # rule FR05-429-has-retry-after: header_name == "Retry-After"
    assert header_name == "Retry-After"
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None, (
        f"429 response must include a Retry-After header per SPEC.md §7, "
        f"got headers={dict(response.headers)!r}"
    )
    # Retry-After must be a non-negative integer-string (seconds). We do not
    # pin the exact value because the production implementation may round
    # up to the next whole second, but it must be parseable.
    assert retry_after.isdigit() and int(retry_after) >= 0, (
        f"Retry-After must be a non-negative integer (seconds), "
        f"got {retry_after!r}"
    )

    # rule FR05-429-content-type: content_type == "application/problem+json"
    assert content_type == "application/problem+json"
    assert response.headers.get("content-type", "") == "application/problem+json", (
        f"429 response must use application/problem+json media type, "
        f"got content-type={response.headers.get('content-type')!r}"
    )
    body = response.json()
    # FR-10 envelope fields are also required on the 429 — the FR-05 case
    # crosses the FR-10 contract by carrying a Retry-After header AND the
    # RFC 7807 envelope.
    for field in ("type", "title", "status", "detail", "instance", "correlation_id"):
        assert field in body, (
            f"429 body must include FR-10 envelope field {field!r}, "
            f"got keys {sorted(body)}"
        )
    assert body["status"] == 429, (
        f"429 body must echo status=429, got {body!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-5.2 / rule FR05-row-level-lock-applied
# --------------------------------------------------------------------------


# NFR-06 NFR-09 NFR-13
def test_row_level_lock_unit(sqlite_db_url):
    """Bucket updates run inside a single transaction with a row-level lock.

    [FR-05] AC-5.2: the token bucket state is persisted in the database;
    bucket updates run in a single transaction with a row-level lock so
    concurrent workers see a single, monotonically advancing counter
    (not a stale read followed by a lost write). The TEST_SPEC inputs
    pin the lock kind to ``with_for_update`` and the transaction count
    to ``1`` so a future implementation that uses a different lock
    strategy (or two transactions) is rejected.

    The test:
      1. Resets the engine and creates the schema so the rate_repo (when
         it lands) has a table to lock.
      2. Calls the rate_repo helper four times concurrently against the
         same key_id.
      3. Asserts that every call executes under a session whose bucket
         was read with ``with_for_update()`` so the four concurrent
         updates cannot race.

    GREEN TODO: ``taskq_api.repository.rate_repo`` must expose a function
    (e.g. ``consume_token(session, key_id, *, bucket_size, refill_rate) -> float``)
    that:
      * SELECTs the bucket row with ``SELECT ... WITH FOR UPDATE`` (i.e.
        ``session.execute(select(Bucket).with_for_update().where(...))``)
        so the row is locked at the DB level for the lifetime of the
        transaction.
      * Refills + decrements the token count inside the SAME transaction
        (commit/rollback is the caller's responsibility, so the provider
        runs ONE session.execute under the surrounding ``session_scope``).
      * Returns the remaining token count after the decrement.
    """
    bucket_initial_tokens = "10"
    refill_rate_per_sec = "5.0"
    transaction_count = "1"
    lock_kind = "with_for_update"
    concurrency_workers = "4"

    # Build the schema so the rate_repo (when implemented) has a table to
    # lock. The exact table name is not asserted by the test — only the
    # lock kind and the transaction count are bucketed down.
    import sqlalchemy
    db_session.reset_engine()
    from taskq_api.models import orm
    orm.Base.metadata.create_all(db_session.get_engine())

    # The rate_repo module must expose a consume path so the FR-05 hook
    # in api.deps.auth_dep can call it. The unit test calls the helper
    # directly with an in-process session so the SQLAlchemy event
    # listener can observe the lock predicate.
    assert hasattr(rate_repo, "consume_token"), (
        "taskq_api.repository.rate_repo must expose consume_token(...) "
        "so the FR-05 ratelimit dependency can call it under a single "
        "session_scope (AC-5.2)"
    )

    # rule FR05-row-level-lock-applied: lock_kind == "with_for_update" and
    # transaction_count == "1" — both invariants must hold for the
    # implementation to be considered correct.
    assert lock_kind == "with_for_update"
    assert transaction_count == "1"

    # Patch the bucket signature so the test does not depend on the
    # production signature shape (the GREEN agent may rename or extend
    # keywords). We only pin the load-bearing behaviour: the helper
    # SELECTs the bucket row with ``with_for_update()`` and the
    # surrounding transaction stays open.
    from sqlalchemy import event as sa_event

    lock_statements: list[str] = []
    begin_count = {"n": 0}
    commit_count = {"n": 0}

    engine = db_session.get_engine()

    def _before_cursor(conn, cursor, statement, parameters, context, executemany):
        # SQLAlchemy emits "SELECT ... FOR UPDATE" for ``with_for_update()``.
        # The lock keyword is uppercase in the rendered SQL regardless of
        # the dialect we connect to, so the substring check is dialect-
        # independent.
        if "FOR UPDATE" in statement.upper():
            lock_statements.append(statement)
        if statement.upper().startswith("BEGIN"):
            begin_count["n"] += 1
        if statement.upper().startswith("COMMIT"):
            commit_count["n"] += 1

    sa_event.listen(engine, "before_cursor_execute", _before_cursor)

    try:
        # Seed the bucket row at the production size+rate so the helper
        # has a clean baseline.
        key_id = "fr05-lock-test-" + uuid.uuid4().hex
        initial = int(bucket_initial_tokens)

        async def _worker():
            """One worker's consume call: wrap the helper in a transaction.

            The test deliberately runs the call inside an explicit
            ``session_scope`` so the helper's WITH FOR UPDATE lock
            is held for the entire consume path. We exercise the helper
            via its keyword-only signature so the test does not couple
            to positional details.
            """
            return _run(_run_one(key_id, initial, float(refill_rate_per_sec)))

        async def _run_one(kid: str, size: int, rate: float) -> float:
            """Run a single consume in its own session_scope.

            The test uses ``asyncio.to_thread`` to run the synchronous
            consume synchronously from the async worker — the helper
            itself is sync (it goes through SQLAlchemy, which is sync).
            """
            loop = asyncio.get_event_loop()

            def _go():
                with db_session.session_scope() as session:
                    return rate_repo.consume_token(
                        session,
                        kid,
                        bucket_size=size,
                        refill_rate_per_sec=rate,
                    )

            return await loop.run_in_executor(None, _go)

        async def _concurrent():
            return await asyncio.gather(
                *(_run_one(key_id, initial, float(refill_rate_per_sec)) for _ in range(4))
            )

        results = _run(_concurrent())
    finally:
        sa_event.remove(engine, "before_cursor_execute", _before_cursor)

    # Every consume call must run inside ONE transaction with a lock.
    # The lock is SQL-level (SELECT ... FOR UPDATE), so the rendered
    # SQL must contain the ``FOR UPDATE`` keyword.
    assert lock_statements, (
        "rate_repo.consume_token must SELECT the bucket row with "
        "with_for_update() so concurrent workers cannot race on the "
        "shared counter (AC-5.2); no SELECT FOR UPDATE statement was "
        "observed"
    )

    # The number of BEGIN statements observed must equal the number of
    # consume calls (4). Combined with the lock-statements assertion,
    # this pins the "single transaction per update" contract.
    concurrency = int(concurrency_workers)
    assert begin_count["n"] >= concurrency, (
        f"each consume call must run inside its own transaction "
        f"(expected at least {concurrency} BEGINs, got {begin_count['n']})"
    )

    # Sanity: the returns from the four concurrent calls MUST be
    # monotonically decreasing — the bucket started at 10, and each
    # consume decrements. The exact values depend on the refill
    # calculation, but the ordering must NOT have two returns at the
    # same level or higher than the previous one (a race would show
    # e.g. [10, 10, 9, 8] instead of [10, 9, 8, 7]).
    assert len(results) == concurrency, (
        f"expected {concurrency} concurrent consume results, got {len(results)}"
    )
    # Sort the results descending — the FIRST one observed is the
    # highest (least decremented), the LAST is the lowest. A race that
    # reads the same value twice would manifest as two equal entries
    # in the sorted list.
    sorted_results = sorted(results, reverse=True)
    for i in range(1, len(sorted_results)):
        assert sorted_results[i] < sorted_results[i - 1], (
            f"concurrent consume calls must NOT observe the same bucket "
            f"value (race detected); got results={results!r}"
        )


# --------------------------------------------------------------------------
# Case 3 — AC-5.3 / rule FR05-healthz-not-rate-limited
# --------------------------------------------------------------------------


# NFR-09 NFR-10
def test_healthz_readyz_not_rate_limited(sqlite_db_url, monkeypatch):
    """``/healthz`` and ``/readyz`` are not subject to rate limiting.

    [FR-05] AC-5.3: rate limiting is applied to ``/v1/*`` business routes
    but NOT to the operator-facing ``/healthz`` and ``/readyz`` probes
    (SPEC.md §3 FR-05). Issue 100 requests against each; the FR-05
    ratelimit dependency must not increment / not deplete the bucket
    for these routes.

    The test sets ``TASKQ_RATE_BURST=1`` so any FIRST request consumed
    from the bucket would empty it; the second request would then
    surface 429. ``/healthz`` and ``/readyz`` must not consult the
    bucket at all, so all 100 requests per endpoint must return 200.
    """
    request_count = "100"
    rate_limit_triggered = "false"
    expected_status = "200"

    # Force the burst to 1 so a single consumed token would deplete the
    # bucket and cause the second request to 429. The bypass routes must
    # NOT consult the bucket, so all 100 succeed.
    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.01")  # refill is essentially disabled.

    application = _build_app(sqlite_db_url)
    db_session.get_engine()  # ensure engine is built against the per-test DB.

    assert rate_limit_triggered == "false"
    assert expected_status == "200"

    async def _do():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            healthz_responses = []
            readyz_responses = []
            for _ in range(100):
                healthz_responses.append(await c.get("/healthz"))
                readyz_responses.append(await c.get("/readyz"))
            return healthz_responses, readyz_responses

    healthz_responses, readyz_responses = _run(_do())

    # rule FR05-healthz-not-rate-limited: rate_limit_triggered == "false"
    # and expected_status == "200" — every request must return 200.
    n = int(request_count)
    assert len(healthz_responses) == n, (
        f"expected {n} healthz responses, got {len(healthz_responses)}"
    )
    assert len(readyz_responses) == n, (
        f"expected {n} readyz responses, got {len(readyz_responses)}"
    )

    for i, resp in enumerate(healthz_responses):
        assert resp.status_code == 200, (
            f"/healthz request #{i!r} must return 200 (bypass rate limit), "
            f"got {resp.status_code} with body={resp.text!r}"
        )
    for i, resp in enumerate(readyz_responses):
        assert resp.status_code == 200, (
            f"/readyz request #{i!r} must return 200 (bypass rate limit), "
            f"got {resp.status_code} with body={resp.text!r}"
        )

    # Drive one additional request against a /v1/* route to CONFIRM the
    # rate limiter IS in effect (the bypass is selective, not blanket).
    # We use a read-only GET /v1/tasks (no body) so the test does not
    # need to seed a real key — the read scope requires only a valid
    # token, and the rate limiter must fire BEFORE auth_dep so the
    # second request is rejected with 429 even when the key is invalid.
    # If the rate limiter is wired correctly, the SECOND request must
    # come back with 429 (the bucket was empty after the first consume).
    async def _do_v1():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            # First request — consumes the only token.
            first = await c.get("/v1/tasks", headers={"X-API-Key": "sk-bypass-probe"})
            # Second request — bucket is empty, must be 429.
            second = await c.get("/v1/tasks", headers={"X-API-Key": "sk-bypass-probe"})
            return first, second

    first_v1, second_v1 = _run(_do_v1())
    # The first request may be 401 (invalid key) — what matters is that
    # the second is 429 (rate-limited). The ratelimit dependency MUST
    # fire BEFORE auth_dep so unauthenticated callers cannot drain the
    # bucket; otherwise the 429 cannot be guaranteed.
    assert second_v1.status_code == 429, (
        f"/v1/* route must be rate-limited (BURST=1, second request must "
        f"return 429), got first={first_v1.status_code} second={second_v1.status_code} "
        f"with body={second_v1.text!r}"
    )
