"""FastAPI dependencies (auth, scope) shared by every /v1/* route.

[FR-01] Every ``/v1/*`` endpoint runs through ``auth_dep`` exactly once.
That is the single-dependency rule that FR-04 case 2's
``test_all_v1_routes_use_same_dep`` test pins down — and the way FR-01's
own CRUD routes stay scope-checked without each handler having to invent a
new check.

[FR-03] ``auth_dep`` now consults the production ``api_keys`` table via
``taskq_api.service.auth`` and ``taskq_api.repository.key_repo``. A missing
``X-API-Key``, an unknown hash, or a row with ``revoked_at`` set all
surface as ``UnauthorizedError`` → 401 + ``application/problem+json``
(``type=/errors/unauthorized``). The detail string is intentionally generic
(NFR-04) so the response cannot distinguish "missing" from "revoked" from
"unknown".

[FR-04] AC-4.1: a failed scope check raises ``ForbiddenError`` → 403 +
``application/problem+json`` with ``type=/errors/forbidden``. The detail
string is intentionally generic so the response cannot be used to probe
whether the resource id exists. AC-4.2: ``auth_dep`` is the single auth
dependency — every /v1 route passes through it, and the scope check is
applied per-route via ``check_scope`` rather than scattered across
handlers.

[FR-05] ``rate_dep`` charges one token against the resolved principal's
DB-persisted bucket and raises ``RateLimitedError`` → 429 +
``application/problem+json`` + ``Retry-After`` when the bucket is empty. It
is invoked from inside ``auth_dep`` so the routes keep the single-dependency
shape AC-4.2 requires, and so unauthenticated probes (``/healthz``,
``/readyz``) — which never run ``auth_dep`` — stay exempt (AC-5.3).

Citations:
- SPEC.md#L101-L107 (FR-03 — X-API-Key, hmac.compare_digest, revoked)
- SPEC.md#L109-L113 (FR-04 — single dependency, no leak in 403)
- SPEC.md#L115-L120 (FR-05 — per-token 令牌桶, 429 + Retry-After)
- SAD.md#L161-L172 (§2.4 `api/deps.py` — auth_dep, scope check)
- SRS.md#L92-L131 (AC-1.1..AC-1.7, scope per row of FR-01/02)
- SRS.md (AC-4.1 / AC-4.2 / AC-4.3)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import NoReturn

from fastapi import Header
from starlette.requests import Request

from taskq_api.errors import ForbiddenError, UnauthorizedError
from taskq_api.repository import key_repo
from taskq_api.repository.session import session_scope
from taskq_api.service import auth as auth_service
from taskq_api.service import ratelimit

__all__ = [
    "Principal",
    "auth_dep",
    "check_scope",
    "rate_dep",
    "bind_correlation_id",
    "problem_instance",
]


# [FR-10 / FR-09] Header carrying the correlation id. Aliased here so the
# dependency signature, the middleware that echoes it on the response, and
# any future header rename stay in lock-step.
CORRELATION_ID_HEADER = "X-Correlation-Id"

# [FR-10] The one status whose envelope may not echo the request target —
# SPEC.md §7's 403 row is the only 不洩漏資源是否存在 rule in the table.
_NON_DISCLOSING_STATUS = 403


# ---------------------------------------------------------------------------
# Module-level constants — shared response details and the auth header name.
# ---------------------------------------------------------------------------

# [FR-03 / NFR-04] The detail string is intentionally identical across every
# 401 path so the response cannot be used to probe which keys exist (missing
# vs. revoked vs. unknown all surface the same string).
_UNAUTHORIZED_DETAIL = "missing or invalid API key"

# [FR-04 / NFR-02] The 403 detail is intentionally generic — it must NOT
# echo back the task id, the action, or any wording that would let an
# attacker probe whether the resource exists (per AC-4.1).
_FORBIDDEN_DETAIL = "insufficient scope"

# [FR-03] The HTTP header that carries the API key. Aliased here so the
# dependency signature and any future header rename stay in lock-step.
_API_KEY_HEADER = "X-API-Key"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, surfaced to every handler via ``auth_dep``.

    [FR-01] Carries only the two fields the test fixture supplies: a key id
    and a scope. In FR-03 this expands to a real API-key lookup; the
    handler-facing shape stays the same.
    """

    key_id: str
    scope: str


def _reject_unauthorized() -> NoReturn:
    """Raise the canonical 401 with the non-disclosing detail string.

    Centralised so the missing / unknown / revoked paths all surface the
    identical NFR-04 detail — an operator cannot use the response to
    distinguish between them. Typed ``NoReturn`` so callers can use the
    function as a type-narrowing guard (pyright only).
    """
    raise UnauthorizedError(_UNAUTHORIZED_DETAIL)


def _resolve_principal(x_api_key: str) -> Principal:
    """Look up ``x_api_key`` and return its ``Principal``, or reject as 401.

    [FR-03] AC-3.1 / AC-3.4: a key whose ``api_keys`` row carries a
    non-null ``revoked_at``, or whose hash does not match, surfaces as a
    generic 401 (NFR-04). AC-3.3: the constant-time ``hmac.compare_digest``
    oracle in ``auth_service.verify_key`` is the timing-safe comparison.

    The SQL filter and the constant-time verification are deliberately
    chained: the SQL filter excludes revoked rows early (AC-3.4) and the
    hash check is the closed-form equality oracle (defence in depth —
    even a future code path that bypasses the SQL filter cannot accept
    a wrong key without matching the stored hash).
    """
    try:
        with session_scope() as session:
            row = key_repo.lookup_active_key(x_api_key, session=session)
    except Exception:
        # Any DB / driver / pool failure during key lookup is surfaced as a
        # generic 401 — the detail string is the same as for a missing key so
        # the response cannot be used to probe DB health (NFR-04).
        _reject_unauthorized()
    if row is None or not auth_service.verify_key(x_api_key, row["key_hash"]):
        _reject_unauthorized()
    return Principal(key_id=row["id"], scope=row["scope"])


def rate_dep(principal: Principal) -> Principal:
    """Charge one rate-limit token to ``principal``; raise 429 when empty.

    [FR-05] AC-5.1 / AC-5.3: the limit is per API key, so the charge happens
    once the caller is identified — an unauthenticated request is rejected as
    401 before it can spend (or create) any key's bucket. Because the charge
    hangs off ``auth_dep``, it applies to exactly the routes that authenticate
    (every ``/v1/*`` route) and to no others: the operator probes ``/healthz``
    and ``/readyz`` are unauthenticated and therefore never rate limited.

    Returning the principal keeps this composable inside ``auth_dep`` without
    changing the single-dependency shape every ``/v1`` route relies on
    (FR-04 AC-4.2).

    Citations:
    - SPEC.md#L115-L120 (FR-05 — per-token 令牌桶, healthz/readyz 不受限)
    - SAD.md#L211-L217 (§3.1 — api/deps hosts auth_dep / scope_dep / rate_dep)
    """
    ratelimit.consume(principal.key_id)
    return principal


def auth_dep(
    x_api_key: str | None = Header(default=None, alias=_API_KEY_HEADER),
) -> Principal:
    """Return the authenticated, rate-limit-cleared principal for the request.

    [FR-03] AC-3.1: a missing ``X-API-Key`` header is rejected as 401 +
    ``/errors/unauthorized``. AC-3.4: a key whose ``api_keys`` row carries
    a non-null ``revoked_at`` is rejected the same way. The
    ``detail`` field stays generic so the response cannot be used to
    probe which keys exist (NFR-04).

    [FR-05] Once the principal is resolved, ``rate_dep`` charges one token
    against that key's bucket and raises 429 when it is empty (AC-5.1).
    """
    if not x_api_key:
        _reject_unauthorized()
    return rate_dep(_resolve_principal(x_api_key))


def check_scope(principal: Principal, needed: str) -> None:
    """Raise 403 if ``principal.scope`` does not satisfy ``needed``.

    [FR-04] AC-4.1: a failed check surfaces as ``ForbiddenError`` → 403 +
    ``application/problem+json`` (``type=/errors/forbidden``). The detail
    string is intentionally generic so the response cannot be used to
    probe which resource id was being authorised (NFR-02 / FR-04
    non-disclosure).

    The rank comparison is delegated to ``taskq_api.service.auth.scope_satisfies``
    so the read < write < admin hierarchy is defined in exactly one place
    (AC-4.3).
    """
    if not auth_service.scope_satisfies(needed, principal.scope):
        raise ForbiddenError(_FORBIDDEN_DETAIL)


def bind_correlation_id(request: Request) -> str:
    """Resolve the correlation id for ``request`` and bind it to its state.

    [FR-10] SAD.md §2.5 names this as one of the two required ``api/`` hub
    functions — alongside ``problem_response`` — so the correlation id is
    resolved in exactly one place. The middleware that echoes it on the
    response, the log line that records it, and every problem envelope that
    surfaces it all read from the same source.

    Inbound wins: an ``X-Correlation-Id`` request header is adopted verbatim
    so a caller can stitch its own id end to end (AC-10.3). When the header
    is absent, a fresh uuid4 is minted so every response stays stitchable.
    The resolved id is stored on ``request.state.correlation_id`` for the
    rest of the ASGI chain to read.

    Citations:
    - SPEC.md#L162-L168 (FR-10 — correlation_id in header + log)
    - SPEC.md#L208-L213 (NFR-10 — observability)
    - SAD.md §2.5 (`api/` hub functions — bind_correlation_id, problem_response)
    """
    # Inbound wins; an absent or empty header falls through to a fresh uuid4.
    cid = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
    request.state.correlation_id = cid
    return cid


def problem_instance(request: Request, status: int) -> str:
    """Return the RFC 7807 ``instance`` value for a ``status`` response.

    [FR-10] ``instance`` identifies the specific occurrence of the problem
    (RFC 7807 §3.1), and the request path is the natural identifier — except
    for HTTP 403, where SPEC.md §7 spells out ``不洩漏資源是否存在``. The
    request path of a scope failure embeds the target resource id, so
    echoing it back would put that id in the envelope the FR-04 /
    NFR-02 non-disclosure rule governs. 403 therefore carries no occurrence
    URI at all; the ``correlation_id`` field remains the operator's handle
    on that specific request.

    Citations:
    - SPEC.md#L165 (FR-10 — body 欄位 ... instance ...)
    - SPEC.md#L394 (§7 — 權限不足 | 403 | `/errors/forbidden` 不洩漏資源是否存在)
    - SPEC.md#L191 (NFR-02 — 403 回應不得洩漏資源存在性)
    """
    if status == _NON_DISCLOSING_STATUS:
        return ""
    return request.url.path
