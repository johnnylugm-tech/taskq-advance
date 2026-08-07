"""RFC 7807 ``application/problem+json`` error contract (independence layer).

[FR-01] FR-01's non-2xx surface — 422 validation, 404 unknown task, 409
duplicate name — is expressed entirely through the envelope built here, so
the media type and body shape are defined in exactly one place.

The domain exceptions below deliberately carry no ``fastapi``/``starlette``
import: the service layer raises them, and ``taskq_api.app`` is the only
module that translates them into HTTP responses. That keeps the layer
contract (NFR-06) intact — service never imports the web framework.

``detail`` is always a caller-facing sentence. Stack traces, SQL text,
filesystem paths and schema descriptions must never reach it (NFR-04, FR-10).

Citations:
- SPEC.md#L162-L168 (FR-10 — problem+json, body fields, no internal leakage)
- SPEC.md#L385-L402 (§7 錯誤處理 — status → `type` URI mapping)
- SAD.md#L118-L123 (§2.4 `taskq_api.errors` — envelope builder, stdlib only)
- SRS.md#L112-L122 (AC-1.2 / AC-1.3 / AC-1.4 — 422 / 404 / 409 + problem+json)
"""

__all__ = [
    "PROBLEM_MEDIA_TYPE",
    "TYPE_VALIDATION",
    "TYPE_NOT_FOUND",
    "TYPE_CONFLICT",
    "TYPE_INTERNAL",
    "TYPE_UNAUTHORIZED",
    "TYPE_FORBIDDEN",
    "ProblemError",
    "NotFoundError",
    "ConflictError",
    "ValidationProblem",
    "UnauthorizedError",
    "ForbiddenError",
    "problem",
]

# SPEC.md §7: every non-2xx response carries this exact media type. The tests
# compare `response.headers["content-type"]` for equality, so no charset
# parameter may be appended.
PROBLEM_MEDIA_TYPE = "application/problem+json"

# SPEC.md §7 `type` URIs.
TYPE_VALIDATION = "/errors/validation"
TYPE_NOT_FOUND = "/errors/not-found"
TYPE_CONFLICT = "/errors/conflict"
TYPE_INTERNAL = "/errors/internal"
TYPE_UNAUTHORIZED = "/errors/unauthorized"
TYPE_FORBIDDEN = "/errors/forbidden"


class ProblemError(Exception):
    """A domain failure that maps onto one row of SPEC.md §7.

    [FR-01] Raised by the service layer; rendered by the handler registered
    in ``taskq_api.app``. Carrying the status/type/title on the exception is
    what lets the service stay free of any HTTP dependency.

    Citations:
    - SPEC.md#L385-L402 (§7 錯誤處理 — 情況 → HTTP → `type`)
    - SAD.md#L146-L157 (§2.4 service — no ORM/HTTP types leaked)
    """

    status = 500
    type_uri = TYPE_INTERNAL
    title = "Internal Server Error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ProblemError):
    """Unknown resource id → HTTP 404 (AC-1.3).

    [FR-01] ``detail`` stays deliberately generic so the response cannot be
    used to probe which ids exist (NFR-02, FR-04's non-disclosure rule).

    Citations:
    - SPEC.md#L89 (FR-01 — 未知 id → HTTP 404 + problem+json)
    - SPEC.md#L392 (§7 — 未知 task id | 404 | `/errors/not-found`)
    - SRS.md#L116-L118 (AC-1.3)
    """

    status = 404
    type_uri = TYPE_NOT_FOUND
    title = "Not Found"


class ConflictError(ProblemError):
    """Duplicate task name → HTTP 409 (AC-1.4).

    [FR-01] Signalled by the database's unique constraint on ``tasks.name``
    rather than a read-then-write check, so concurrent creates cannot both
    observe "name is free" and both succeed.

    Citations:
    - SPEC.md#L88 (FR-01 — 名稱唯一)
    - SPEC.md#L393 (§7 — 任務名稱衝突 | 409 | `/errors/conflict`)
    - SRS.md#L119-L122 (AC-1.4)
    """

    status = 409
    type_uri = TYPE_CONFLICT
    title = "Conflict"


class ValidationProblem(ProblemError):
    """Request failed schema validation → HTTP 422 (AC-1.2 / AC-1.6).

    Citations:
    - SPEC.md#L88 (FR-01 — 違反驗證規則 → HTTP 422 + problem+json)
    - SPEC.md#L390 (§7 — 請求 body 驗證失敗 | 422 | `/errors/validation`)
    - SRS.md#L112-L115 (AC-1.2), SRS.md#L126-L128 (AC-1.6)
    """

    status = 422
    type_uri = TYPE_VALIDATION
    title = "Unprocessable Entity"


class UnauthorizedError(ProblemError):
    """Missing or invalid API key → HTTP 401 (FR-03 AC-3.1 / AC-3.4).

    [FR-03] Raised by ``auth_dep`` when the ``X-API-Key`` header is absent,
    the candidate hash does not match any stored ``api_keys`` row, or the
    matched row carries a non-null ``revoked_at``. The detail string stays
    deliberately generic (per NFR-04) so the response cannot be used to
    distinguish "missing" from "revoked" from "unknown".

    Citations:
    - SPEC.md#L101-L107 (FR-03 — X-API-Key, hmac.compare_digest, revoked)
    - SPEC.md#L391 (§7 — 缺少 / 無效 API key | 401 | `/errors/unauthorized`)
    """

    status = 401
    type_uri = TYPE_UNAUTHORIZED
    title = "Unauthorized"


class ForbiddenError(ProblemError):
    """Insufficient scope for the requested route → HTTP 403 (FR-04 AC-4.1).

    [FR-04] Raised by ``api.deps.check_scope`` when the authenticated
    principal's scope rank is strictly below the route's required scope.
    The ``detail`` string is intentionally generic — it MUST NOT echo
    back the resource id, the action, or any wording that would let an
    attacker probe whether the id exists (NFR-02 / FR-04 non-disclosure).

    Citations:
    - SPEC.md#L109-L113 (FR-04 — scope check, 403, no existence leak)
    - SPEC.md#L394 (§7 — 權限不足 | 403 | `/errors/forbidden`)
    - SRS.md (AC-4.1 — 403 + non-leaking body)
    """

    status = 403
    type_uri = TYPE_FORBIDDEN
    title = "Forbidden"


def problem(
    status: int,
    type_uri: str,
    title: str,
    detail: str,
    instance: str,
    correlation_id: str,
) -> dict:
    """Build the RFC 7807 body for a non-2xx response.

    [FR-01] Emits the six fields FR-10 fixes — ``type``, ``title``,
    ``status``, ``detail``, ``instance``, ``correlation_id`` — and nothing
    else, so no internal state can ride along in an unreviewed key.

    Citations:
    - SPEC.md#L165 (FR-10 — body 欄位 type/title/status/detail/instance/correlation_id)
    - SPEC.md#L166 (FR-10 — detail 不得洩漏內部細節)
    """
    return {
        "type": type_uri,
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "correlation_id": correlation_id,
    }
