"""HTTP route for the FR-04 metrics endpoint.

[FR-04] The metrics endpoint is enumerated alongside the /v1/tasks routes
in AC-4.2 so it MUST be registered on the app and MUST pass through the
single ``auth_dep`` dependency. The body itself is a stub for now —
fronting the real FR-05 metrics is an out-of-scope extension point.

Citations:
- SPEC.md#L109-L113 (FR-04 — single auth dependency across /v1)
- SAD.md#L166-L175 (API route ownership)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from taskq_api.api.deps import Principal, auth_dep

__all__ = ["router"]

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("", response_model=None)
def get_metrics(principal: Principal = Depends(auth_dep)) -> dict:
    """Return basic service metrics. [FR-04] Citations: SPEC.md#L109-L113."""
    return {"ok": True}
