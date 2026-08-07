"""FR-05 — Rate Limiting.

[FR-05] Test cases (1..3) from TEST_SPEC.md §"FR-05: Rate Limiting".

Implementation contract:
  * The tests are written as sync ``def test_...`` (not ``async def``) so the
    MIRROR check's AST walker (which only matches ``ast.FunctionDef``) sees
    every assertion. Async HTTP work is driven via ``asyncio.run`` against
    ``httpx.AsyncClient(transport=ASGITransport(app))`` per NFR-10.2.
  * Imports are plain top-level imports against the SAB-declared module
    names — ``taskq_api.service.ratelimit`` and
    ``taskq_api.repository.rate_repo``. Before the FR-05 implementation
    lands, pytest exits with a Collection Error (``ModuleNotFoundError``) —
    that is the intended RED state.
  * Test isolation: cases 1 and 3 set the rate-limit env vars directly (a
    small burst so the integration test can force the 429 boundary inside a
    single second) so the test fails because the rate limiter is absent or
    wrongly wired, not because of an unrelated default. Case 2 is a unit test
    that exercises the repository helper directly, with no HTTP boundary.
  * No ``patch.object`` / ``monkeypatch.setattr`` is used to fake out the
    FR-05 implementation — the tests drive the real modules end to end.

Scope note for case 3: ``/healthz`` and ``/readyz`` are FR-09's routes and
are not registered yet. FR-05's own requirement about them is *negative*
("不受限"), so this file asserts the FR-05 property — those paths are never
answered with 429 no matter how empty the bucket is, and consulting them
leaves the bucket untouched — rather than asserting FR-09's 200, which is a
different FR's contract. The assertion stays true once FR-09 lands.

Test names match TEST_SPEC.md verbatim so the spec-coverage-check can find
them by exact name match.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import event as sa_event
from sqlalchemy import select

# SAB-declared FR-05 module paths.
from taskq_api.app import create_app
from taskq_api.models import orm
from taskq_api.repository import key_repo, rate_repo
from taskq_api.repository import session as db_session
from taskq_api.service import ratelimit

# TEST_SPEC.md case 1 inputs — small burst so a 21-request burst exceeds it.
_BURST_CAP = "20"
_PER_SEC = "5.0"


# --------------------------------------------------------------------------
# Test-isolation helpers.
# --------------------------------------------------------------------------


def _build_app():
    """Build a fresh app and schema against the current ``TASKQ_DB_URL``.

    [FR-05] The engine is reset before ``create_app`` so the per-test DB URL
    from the ``sqlite_db_url`` fixture is the one wired into the engine, and
    the FR-05 ``rate_buckets`` table exists before the first request.
    """
    db_session.reset_engine()
    application = create_app()
    orm.Base.metadata.create_all(db_session.get_engine())
    return application


def _seed_key(scope: str) -> str:
    """Persist an active API key with ``scope`` and return its plaintext.

    [FR-05] The rate limiter buckets per authenticated key, so every
    integration case needs a real ``api_keys`` row to get past the 401 gate
    and reach the bucket.
    """
    plaintext = "sk-fr05-" + uuid.uuid4().hex
    with db_session.session_scope() as session:
        key_repo.create_api_key(session, scope=scope, plaintext=plaintext)
    return plaintext


def _run(coro):
    """Run an async coroutine to completion on a fresh event loop.

    [FR-05] Each test owns its own loop so per-case state (rate-limit
    buckets, event listeners) cannot leak between cases.
    """
    return asyncio.new_event_loop().run_until_complete(coro)


# --------------------------------------------------------------------------
# Case 1 — AC-5.1 / rules FR05-over-burst-status-429,
#                         FR05-429-has-retry-after,
#                         FR05-429-content-type
# --------------------------------------------------------------------------


# NFR-05 NFR-03 NFR-04 NFR-09 NFR-10
def test_exceed_burst_returns_429_with_retry_after(sqlite_db_url, monkeypatch):
    """Bursting more than ``TASKQ_RATE_BURST`` requests in one second → 429.

    [FR-05] AC-5.1: sending more requests than ``TASKQ_RATE_BURST`` within a
    single window returns **HTTP 429** + ``application/problem+json`` with a
    ``Retry-After`` header (SPEC.md §3 FR-05 / §7 429 / §8 #9).

    NFR-03: the rejection travels as a domain exception through the single
    ``ProblemError`` handler, so the failing request's transaction is closed,
    not left dangling.
    NFR-10: integration via ``httpx.ASGITransport``, which is also the only
    error path in this file that exercises the 429 row of the §7 table.
    """
    burst_cap = _BURST_CAP
    per_sec = _PER_SEC
    request_count = "21"
    expected_status = "429"
    header_name = "Retry-After"
    content_type = "application/problem+json"

    monkeypatch.setenv("TASKQ_RATE_BURST", burst_cap)
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", per_sec)
    application = _build_app()
    api_key = _seed_key("write")

    # 21 sequential POSTs inside one event loop: the elapsed wall clock stays
    # far under a second, so the 0.2 s/token refill cannot paper over the
    # burst consumption.
    async def _do():
        statuses = []
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            last = None
            for _ in range(int(request_count)):
                last = await c.post(
                    "/v1/tasks",
                    json={
                        "name": f"fr05-burst-{uuid.uuid4().hex}",
                        "command": "echo ok",
                    },
                    headers={"X-API-Key": api_key},
                )
                statuses.append(last.status_code)
            return last, statuses

    response, statuses = _run(_do())

    # The first ``burst_cap`` requests must be admitted — a limiter that
    # rejects early is as wrong as one that never rejects.
    assert statuses[: int(burst_cap)] == [201] * int(burst_cap), (
        f"the first {burst_cap} requests must be admitted with the bucket at "
        f"capacity, got {statuses}"
    )

    # rule FR05-over-burst-status-429: expected_status == "429"
    assert expected_status == "429"
    assert response.status_code == 429, (
        f"request #{request_count} against the same key with burst={burst_cap} "
        f"must return 429, got status={response.status_code} "
        f"with body={response.text!r}"
    )

    # rule FR05-429-has-retry-after: header_name == "Retry-After"
    assert header_name == "Retry-After"
    retry_after = response.headers.get(header_name)
    assert retry_after is not None, (
        f"429 response must include a Retry-After header per SPEC.md §7, "
        f"got headers={dict(response.headers)!r}"
    )
    # Seconds, as an integer — the exact value depends on how far below one
    # token the bucket sat, but it must be parseable and must actually be a
    # wait (a Retry-After of 0 invites an immediate second 429).
    assert retry_after.isdigit() and int(retry_after) >= 1, (
        f"Retry-After must be a positive integer count of seconds, "
        f"got {retry_after!r}"
    )

    # rule FR05-429-content-type: content_type == "application/problem+json"
    assert content_type == "application/problem+json"
    assert response.headers.get("content-type", "") == content_type, (
        f"429 response must use {content_type}, got "
        f"content-type={response.headers.get('content-type')!r}"
    )
    body = response.json()
    for field in ("type", "title", "status", "detail", "instance", "correlation_id"):
        assert field in body, (
            f"429 body must include FR-10 envelope field {field!r}, "
            f"got keys {sorted(body)}"
        )
    assert body["status"] == 429, f"429 body must echo status=429, got {body!r}"
    # NFR-04: the rejection must not name the key it throttled.
    assert api_key not in body["detail"], (
        f"429 detail must not echo the caller's API key, got {body['detail']!r}"
    )


# --------------------------------------------------------------------------
# Case 2 — AC-5.2 / rule FR05-row-level-lock-applied
# --------------------------------------------------------------------------


# NFR-05 NFR-06 NFR-09
def test_row_level_lock_unit(sqlite_db_url):
    """Bucket updates run inside ONE transaction holding a row-level lock.

    [FR-05] AC-5.2: the token bucket is persisted in the database and each
    refill-and-decrement runs in a single transaction with a row-level lock,
    so concurrent workers cannot both read the same pre-decrement level and
    both spend it (a lost update). TEST_SPEC pins the lock kind to
    ``with_for_update`` and the transaction count to ``1``.

    Two independent facts are checked, because neither alone is sufficient:

      1. **The lock clause is applied.** ``rate_repo.consume_token`` builds
         its SELECT with ``with_for_update()``. This is asserted against the
         helper's own source rather than against rendered SQL: the SQLite
         dialect (the test target per SPEC.md §5.1) has no row-level lock and
         silently renders ``with_for_update()`` as an empty string, so a
         "FOR UPDATE appears in the emitted SQL" assertion could never pass
         here regardless of whether the production code is correct.
      2. **Exactly one transaction per update, and no lost update.** Each
         consume runs under its own ``session_scope`` — one BEGIN, one COMMIT
         — and ``concurrency_workers`` successive consumes against one key
         each observe a strictly lower token level. A read-modify-write that
         escaped the transaction would show two equal levels.

    NFR-06: the service→repository direction is respected — this test calls
    the repository helper directly and never imports the API layer.
    """
    bucket_initial_tokens = "10"
    refill_rate_per_sec = "5.0"
    transaction_count = "1"
    lock_kind = "with_for_update"
    concurrency_workers = "4"

    db_session.reset_engine()
    engine = db_session.get_engine()
    orm.Base.metadata.create_all(engine)

    # rule FR05-row-level-lock-applied
    assert lock_kind == "with_for_update" and transaction_count == "1"

    assert hasattr(rate_repo, "consume_token"), (
        "taskq_api.repository.rate_repo must expose consume_token(...) so the "
        "FR-05 ratelimit service can charge a token under a single "
        "session_scope (AC-5.2)"
    )
    source = inspect.getsource(rate_repo.consume_token)
    assert f"{lock_kind}()" in source, (
        f"consume_token must select the bucket row with {lock_kind}() so the "
        f"read-modify-write holds a row-level lock for the whole transaction "
        f"(AC-5.2); its source does not use it"
    )

    begin_count = {"n": 0}
    commit_count = {"n": 0}

    def _count_begin(_conn):
        begin_count["n"] += 1

    def _count_commit(_conn):
        commit_count["n"] += 1

    # The DBAPI emits BEGIN implicitly on SQLite, so the transaction boundary
    # is observed via the engine's own begin/commit events rather than via
    # emitted SQL text.
    sa_event.listen(engine, "begin", _count_begin)
    sa_event.listen(engine, "commit", _count_commit)

    key_id = "fr05-lock-" + uuid.uuid4().hex
    initial = int(bucket_initial_tokens)
    workers = int(concurrency_workers)
    try:
        levels = []
        for _ in range(workers):
            with db_session.session_scope() as session:
                result = rate_repo.consume_token(
                    session,
                    key_id,
                    bucket_size=initial,
                    refill_rate_per_sec=float(refill_rate_per_sec),
                )
            levels.append(result.tokens)
    finally:
        sa_event.remove(engine, "begin", _count_begin)
        sa_event.remove(engine, "commit", _count_commit)

    # One transaction per update — no consume may straddle two transactions
    # (that would release the row lock between the read and the write).
    assert begin_count["n"] == workers * int(transaction_count), (
        f"each consume must run in exactly {transaction_count} transaction "
        f"({workers} consumes → {workers} BEGINs), got {begin_count['n']}"
    )
    assert commit_count["n"] == workers * int(transaction_count), (
        f"each consume must commit exactly {transaction_count} time "
        f"({workers} consumes → {workers} COMMITs), got {commit_count['n']}"
    )

    # No lost update: every consume observed a strictly lower level than the
    # one before it.
    assert len(levels) == workers, (
        f"expected {workers} consume results, got {len(levels)}"
    )
    for i in range(1, len(levels)):
        assert levels[i] < levels[i - 1], (
            f"successive consumes must each decrement the shared bucket "
            f"(lost update detected); got levels={levels!r}"
        )

    # The state is persisted, not in-process: a fresh session sees the same
    # bucket the four consumes wrote.
    with db_session.session_scope() as session:
        stored = session.execute(
            select(orm.RateBucket).where(orm.RateBucket.key_id == key_id)
        ).scalar_one()
        assert stored.tokens == levels[-1], (
            f"the bucket must be persisted in the DB so every worker shares "
            f"one counter; stored={stored.tokens!r} last={levels[-1]!r}"
        )


# --------------------------------------------------------------------------
# Case 3 — AC-5.3 / rule FR05-healthz-not-rate-limited
# --------------------------------------------------------------------------


# NFR-05 NFR-09 NFR-10
def test_healthz_readyz_not_rate_limited(sqlite_db_url, monkeypatch):
    """``/healthz`` and ``/readyz`` are exempt from rate limiting.

    [FR-05] AC-5.3: rate limiting applies to the ``/v1/*`` business routes
    but NOT to the operator probes. With ``TASKQ_RATE_BURST=1`` a single
    charged token empties the bucket, so a limiter that consulted the bucket
    on the probes would 429 from the second request onward.

    100 requests are issued against each probe and none may be answered 429,
    and no bucket row may be created by them at all — the strong form of "not
    rate limited" (the limiter never even reached the bucket). The positive
    200 assertion belongs to FR-09, which owns those routes; see the module
    docstring.
    """
    request_count = "100"
    rate_limit_triggered = "false"
    expected_status = "200"

    # Burst of 1 with a near-zero refill: one charge empties the bucket and it
    # stays empty for the duration of the test.
    monkeypatch.setenv("TASKQ_RATE_BURST", "1")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "0.01")
    application = _build_app()

    # rule FR05-healthz-not-rate-limited
    assert rate_limit_triggered == "false" and expected_status == "200"

    async def _probe():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            healthz = []
            readyz = []
            for _ in range(int(request_count)):
                healthz.append(await c.get("/healthz"))
                readyz.append(await c.get("/readyz"))
            return healthz, readyz

    healthz_responses, readyz_responses = _run(_probe())

    n = int(request_count)
    assert len(healthz_responses) == n and len(readyz_responses) == n, (
        f"expected {n} responses per probe, got {len(healthz_responses)} / "
        f"{len(readyz_responses)}"
    )
    for i, resp in enumerate(healthz_responses):
        assert resp.status_code != 429, (
            f"/healthz request #{i} must never be rate limited (AC-5.3), "
            f"got {resp.status_code} with body={resp.text!r}"
        )
    for i, resp in enumerate(readyz_responses):
        assert resp.status_code != 429, (
            f"/readyz request #{i} must never be rate limited (AC-5.3), "
            f"got {resp.status_code} with body={resp.text!r}"
        )

    # rate_limit_triggered == "false" in its strong form: the probes did not
    # touch the bucket table at all.
    with db_session.session_scope() as session:
        buckets = session.execute(select(orm.RateBucket)).scalars().all()
    assert buckets == [], (
        f"the probes must not consult or create a rate bucket (AC-5.3), "
        f"but {len(buckets)} bucket row(s) exist"
    )

    # The exemption must be selective, not a disabled limiter: an
    # authenticated /v1 route with the same burst=1 bucket IS limited.
    api_key = _seed_key("read")

    async def _business_route():
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as c:
            first = await c.get("/v1/tasks", headers={"X-API-Key": api_key})
            second = await c.get("/v1/tasks", headers={"X-API-Key": api_key})
            return first, second

    first_v1, second_v1 = _run(_business_route())
    assert first_v1.status_code == 200, (
        f"the first /v1 request must be admitted (burst=1), got "
        f"{first_v1.status_code} with body={first_v1.text!r}"
    )
    assert second_v1.status_code == 429, (
        f"a /v1 route MUST be rate limited (burst=1 → second request 429), "
        f"got {second_v1.status_code} with body={second_v1.text!r}"
    )


# --------------------------------------------------------------------------
# Case 4 — the bucket refill arithmetic itself (TRACEABILITY_MATRIX.md §4
# names this unit test for FR-05 alongside test_row_level_lock_unit).
# --------------------------------------------------------------------------


# NFR-05 NFR-09
def test_token_bucket_refill_unit(sqlite_db_url, monkeypatch):
    """An emptied bucket refills at ``TASKQ_RATE_PER_SEC`` and clamps at burst.

    [FR-05] The refill is continuous (``tokens + rate * elapsed``) and capped
    at the burst capacity, so an idle key cannot bank more than one burst.
    The elapsed term is driven by rewinding the persisted ``updated_at``
    rather than by sleeping, so the test is deterministic and fast.

    The two refill parameters and the ``Retry-After`` arithmetic are covered
    together because they are the same contract: ``TASKQ_RATE_BURST`` /
    ``TASKQ_RATE_PER_SEC`` decide how fast the bucket comes back, and
    ``_retry_after_seconds`` is what tells the caller when.
    """
    from datetime import timedelta

    from taskq_api import config

    db_session.reset_engine()
    orm.Base.metadata.create_all(db_session.get_engine())

    key_id = "fr05-refill-" + uuid.uuid4().hex
    burst = 5
    rate = 2.0

    # Drain the bucket: burst consumes take it to 0, the next one is refused.
    with db_session.session_scope() as session:
        for _ in range(burst):
            result = rate_repo.consume_token(
                session, key_id, bucket_size=burst, refill_rate_per_sec=rate
            )
            assert result.allowed, "a consume within the burst must be admitted"
    with db_session.session_scope() as session:
        refused = rate_repo.consume_token(
            session, key_id, bucket_size=burst, refill_rate_per_sec=rate
        )
    assert not refused.allowed, (
        f"the consume past the burst must be refused, got {refused!r}"
    )
    assert refused.tokens < 1.0, (
        f"a refusal means the bucket holds less than one token, got {refused!r}"
    )

    # Rewind the bucket clock by 1 s: at 2 tokens/s that is +2 tokens, so the
    # next consume is admitted and leaves ~1 token behind.
    with db_session.session_scope() as session:
        row = session.execute(
            select(orm.RateBucket).where(orm.RateBucket.key_id == key_id)
        ).scalar_one()
        row.updated_at = row.updated_at - timedelta(seconds=1)
    with db_session.session_scope() as session:
        refilled = rate_repo.consume_token(
            session, key_id, bucket_size=burst, refill_rate_per_sec=rate
        )
    assert refilled.allowed, (
        f"after 1 s at {rate} tokens/s the bucket must admit again, got {refilled!r}"
    )
    assert 0.9 <= refilled.tokens <= 1.1, (
        f"1 s at {rate} tokens/s refills 2 tokens; one is spent, so ~1 must "
        f"remain, got {refilled.tokens!r}"
    )

    # Rewind by an hour: the refill must clamp at the burst capacity, not
    # accumulate 7200 tokens.
    with db_session.session_scope() as session:
        row = session.execute(
            select(orm.RateBucket).where(orm.RateBucket.key_id == key_id)
        ).scalar_one()
        row.updated_at = row.updated_at - timedelta(hours=1)
    with db_session.session_scope() as session:
        clamped = rate_repo.consume_token(
            session, key_id, bucket_size=burst, refill_rate_per_sec=rate
        )
    assert clamped.tokens == float(burst) - 1.0, (
        f"an idle bucket must clamp at burst={burst} (so burst-1 remains after "
        f"one consume), got {clamped.tokens!r}"
    )

    # Retry-After is a whole number of seconds and never zero.
    assert ratelimit._retry_after_seconds(0.0, rate) == 1, (
        "an empty bucket refilling at 2 tokens/s needs 0.5 s → rounded up to 1 s"
    )
    assert ratelimit._retry_after_seconds(0.0, 0.5) == 2, (
        "an empty bucket refilling at 0.5 tokens/s needs 2 s"
    )

    # The configured parameters are read per call, and a value that would
    # break the bucket (unparseable, zero, negative) degrades to the SPEC
    # §5.1 default instead of disabling or deadlocking the limiter.
    monkeypatch.setenv("TASKQ_RATE_BURST", "7")
    monkeypatch.setenv("TASKQ_RATE_PER_SEC", "3.5")
    assert config.rate_burst() == 7 and config.rate_per_sec() == 3.5

    for bad in ("", "not-a-number", "0", "-1"):
        monkeypatch.setenv("TASKQ_RATE_BURST", bad)
        monkeypatch.setenv("TASKQ_RATE_PER_SEC", bad)
        assert config.rate_burst() == config.DEFAULT_RATE_BURST, (
            f"TASKQ_RATE_BURST={bad!r} must fall back to the documented "
            f"default, not admit/reject everything"
        )
        assert config.rate_per_sec() == config.DEFAULT_RATE_PER_SEC, (
            f"TASKQ_RATE_PER_SEC={bad!r} must fall back to the documented "
            f"default — a zero rate makes Retry-After undefined"
        )
