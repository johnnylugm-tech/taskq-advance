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

Citations:
- SPEC.md#L101-L107 (FR-03 — X-API-Key, hmac.compare_digest, revoked)
- SPEC.md#L109-L113 (FR-04 — single dependency, no leak in 403)
- SAD.md#L161-L172 (§2.4 `api/deps.py` — auth_dep, scope check)
- SRS.md#L92-L131 (AC-1.1..AC-1.7, scope per row of FR-01/02)
- SRS.md (AC-4.1 / AC-4.2 / AC-4.3)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NoReturn

from fastapi import Header

from taskq_api.errors import ForbiddenError, UnauthorizedError
from taskq_api.repository import key_repo
from taskq_api.repository.session import session_scope
from taskq_api.service import auth as auth_service

__all__ = ["Principal", "auth_dep", "check_scope"]


# [FR-03 / NFR-04] The detail string is intentionally identical across every
# 401 path so the response cannot be used to probe which keys exist (missing
# vs. revoked vs. unknown all surface the same string).
_UNAUTHORIZED_DETAIL = "missing or invalid API key"

# [FR-04 / NFR-02] The 403 detail is intentionally generic — it must NOT
# echo back the task id, the action, or any wording that would let an
# attacker probe whether the resource exists (per AC-4.1).
_FORBIDDEN_DETAIL = "insufficient scope"


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


def auth_dep(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """Return the authenticated principal for the request.

    [FR-03] AC-3.1: a missing ``X-API-Key`` header is rejected as 401 +
    ``/errors/unauthorized``. AC-3.4: a key whose ``api_keys`` row carries
    a non-null ``revoked_at`` is rejected the same way. The
    ``detail`` field stays generic so the response cannot be used to
    probe which keys exist (NFR-04).
    """
    if not x_api_key:
        _reject_unauthorized()

    # [FR-03] The lookup compares hashes via ``hmac.compare_digest``
    # (AC-3.3) and excludes revoked rows by SQL filter (AC-3.4). The
    # repository's dict return shape carries the row's ``scope`` so the
    # downstream ``check_scope`` call still works without a separate query.
    with session_scope() as session:
        row = key_repo.lookup_active_key(x_api_key, session=session)
    if row is None:
        _reject_unauthorized()

    # The constant-time verification is also run in the service layer so
    # the equality oracle stays closed even if a future code path skips
    # the SQL filter (defence in depth).
    if not auth_service.verify_key(x_api_key, row["key_hash"]):
        _reject_unauthorized()

    return Principal(key_id=row["id"], scope=row["scope"])


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
