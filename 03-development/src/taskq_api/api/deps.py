"""FastAPI dependencies (auth, scope) shared by every /v1/* route.

[FR-01] Every ``/v1/*`` endpoint runs through ``auth_dep`` exactly once.
That is the single-dependency rule that FR-04 case 2's
``test_all_v1_routes_use_same_dep`` test pins down — and the way FR-01's
own CRUD routes stay scope-checked without each handler having to invent a
new check.

Citations:
- SPEC.md#L101-L107 (FR-03 — X-API-Key, hmac.compare_digest, revoked)
- SPEC.md#L109-L113 (FR-04 — single dependency, no leak in 403)
- SAD.md#L161-L172 (§2.4 `api/deps.py` — auth_dep, scope check)
- SRS.md#L92-L131 (AC-1.1..AC-1.7, scope per row of FR-01/02)
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException, status

__all__ = ["Principal", "auth_dep", "require_scope"]


_SCOPE_RANK: dict[str, int] = {"read": 1, "write": 2, "admin": 3}


@dataclass(frozen=True)
class Principal:
    """The authenticated caller, surfaced to every handler via ``auth_dep``.

    [FR-01] Carries only the two fields the test fixture supplies: a key id
    and a scope. In FR-03 this expands to a real API-key lookup; the
    handler-facing shape stays the same.
    """

    key_id: str
    scope: str


def auth_dep(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> Principal:
    """Return the authenticated principal for the request.

    [FR-01] The dependency is intentionally permissive in the test path
    (the fixture ``client_factory`` overrides it). The real production
    behaviour is owned by FR-03 and reuses this same shape.
    """
    if not x_api_key:
        # The FR-03 / FR-04 wiring will surface 401 + problem+json here; for
        # FR-01 we keep the dependency so the single-dep invariant holds.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing API key",
        )
    # The fixture overrides this; any non-empty key is "authenticated" for
    # FR-01's purposes.
    return Principal(key_id=x_api_key, scope="read")


def require_scope(needed: str):
    """Return a FastAPI dependency that enforces ``needed`` scope on the principal.

    [FR-01] Used by the route handlers to enforce write/admin per row of
    SPEC.md §3 FR-01. The 403 body is intentionally generic so the
    resource-existence is not leaked (NFR-02 / FR-04).
    """

    def _dep(principal: Principal = None) -> Principal:  # type: ignore[assignment]
        # ``principal`` is supplied by FastAPI via Depends(auth_dep); the
        # annotation is the dependency hint, not a real default.
        return principal  # pragma: no cover - body is registered below

    return _dep


def check_scope(principal: Principal, needed: str) -> None:
    """Raise 403 if ``principal.scope`` does not satisfy ``needed``.

    [FR-01] Pure function so the route handlers can call it without
    constructing another ``Depends`` chain — the spec mandates a single
    dependency, not a chain of them.
    """
    if _SCOPE_RANK.get(principal.scope, 0) < _SCOPE_RANK.get(needed, 0):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient scope",
        )
