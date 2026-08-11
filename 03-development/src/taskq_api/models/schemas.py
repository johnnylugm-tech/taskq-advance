"""Pydantic request/response models for the ``/v1/tasks`` API.

[FR-01] ``TaskCreate`` is the only validator that decides what a 422 looks
like: a route handler never invents a 422 inline. Blacklisted characters
(``;``, ``|``, ``&``, ``$``, backticks, newlines) are the
injection-character blacklist that SPEC.md §3 FR-01 mandates; seeing any of
them in either ``name`` or ``command`` causes ``TaskCreate`` to raise and the
handler to translate that into HTTP 422 + ``application/problem+json``.

Citations:
- SPEC.md#L79-L91 (FR-01 — body validated by ``TaskCreate`` pydantic model)
- SPEC.md#L88 (FR-01 — 注入字元黑名單 → HTTP 422)
- SPEC.md#L122-L128 (FR-06 — pydantic validation at the boundary)
- SAD.md#L131 (`models/__init__.py` re-exports pydantic models)
- SRS.md#L92-L131 (AC-1.1..AC-1.7)
"""

from __future__ import annotations

from typing import FrozenSet
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


# SPEC.md §3 FR-01 — 注入字元黑名單: any of these in name/command → 422.
_INJECTION_BLACKLIST: FrozenSet[str] = frozenset({";", "|", "&", "$", "`", "\n", "\r"})

# SPEC.md §3 FR-01 — ≤1000 字元; this is the only length cap.
_MAX_LEN: int = 1000


class TaskCreate(BaseModel):
    """Inbound body for ``POST /v1/tasks``.

    [FR-01] Empty ``name`` or ``command`` → 422. Blacklisted character → 422.
    Length > 1000 → 422. Unknown fields rejected by ``extra="forbid"``.

    The validator returns the field unchanged on success; Pydantic raises
    ``ValueError`` (which FastAPI maps to 422) on any rule breach.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)

    name: str = Field(..., min_length=1, max_length=_MAX_LEN)
    command: str = Field(..., min_length=1, max_length=_MAX_LEN)

    @field_validator("name", "command")
    @classmethod
    def _reject_blacklist_and_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be empty")
        if any(ch in _INJECTION_BLACKLIST for ch in value):
            raise ValueError("contains blacklisted injection character")
        return value


class TaskRead(BaseModel):
    """Outbound body for ``GET/POST/DELETE /v1/tasks/{id}``.

    [FR-01] Mirrors the persisted row so the client can immediately re-fetch
    what it just created without a second round trip.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    command: str
    status: str


class TaskList(BaseModel):
    """Outbound body for ``GET /v1/tasks`` (cursor-paged).

    [FR-01] ``items`` is the page contents; ``next_cursor`` is the opaque
    pagination handle for the next page, or ``None`` at the tail.
    """

    items: list[TaskRead]
    next_cursor: str | None = None


def new_id() -> str:
    """Generate a fresh task id (UUID4 as string)."""
    return str(uuid4())


# pragma: no error-handling
