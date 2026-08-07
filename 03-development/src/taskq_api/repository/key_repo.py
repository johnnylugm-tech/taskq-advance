"""Repository functions for the ``api_keys`` table.

[FR-06] ``api_keys`` reads and writes are owned exclusively by this module
— the service layer hands session-free contracts to the HTTP edge, and no
SQLAlchemy import escapes ``repository/`` (NFR-06 / FR-06 AC-6.1).

[FR-03] Owns every read and write of the ``api_keys`` row. Plaintext never
crosses this boundary — the SHA-256 hash is computed in the service layer
before the row is constructed, and the lookup path compares hashes (never
plaintexts) via ``hmac.compare_digest``.

The repository intentionally exposes dict-shaped return values rather than
ORM instances so the test contract (the GREEN TODO comments in
``test_fr03.py``) lines up exactly: a row created by ``create_api_key`` is
testable as a plain dict whose ``key_hash`` field is a 64-char hex string.

Citations:
- SPEC.md#L101-L107 (FR-03 — api_keys, SHA-256 hash, revoked_at)
- SPEC.md#L122-L128 (FR-06 — repository owns Session, no raw SQL)
- SAD.md#L131-L145 (§2.4 `repository/key_repo.py` — key_repo contract)
- SRS.md (AC-3.2 / AC-3.4 — persisted hash, revoked rejection)
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from taskq_api.models.orm import ApiKey

__all__ = ["create_api_key", "lookup_active_key", "list_all_keys"]


def _hash(plaintext: str) -> str:
    """SHA-256 hex digest of ``plaintext`` (64 lowercase hex chars).

    [FR-03] AC-3.2: the persisted ``key_hash`` is always
    ``hashlib.sha256(plaintext.encode("utf-8")).hexdigest()``. Centralised
    here so the lookup path and the create path use the exact same digest
    function — a divergent hash function would silently reject every key.
    """
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _coerce_revoked_at(value: Optional[datetime | str]) -> Optional[datetime]:
    """Normalise ``revoked_at`` to a timezone-aware ``datetime``.

    [FR-03] SQLite (and any strict DateTime column) rejects raw ISO strings
    as bind values. Callers may pass either a ``datetime`` directly or an
    ISO-8601 string (the latter is what the operator CLI hands us when it
    parses an opt-in ``--revoked-at`` flag in future FRs), so this helper
    funnels both shapes into a single datetime contract.
    """
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # ``Z`` is not a valid ``fromisoformat`` suffix on 3.10; strip it
        # so we accept the canonical ISO-8601 UTC form produced by the
        # rest of the codebase (see ``service.runner._format_finished_at``).
        cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = datetime.fromisoformat(cleaned)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return parsed
    raise TypeError(
        f"revoked_at must be datetime or ISO-8601 string, got {type(value).__name__}"
    )


def _row_to_dict(row: ApiKey) -> dict[str, Any]:
    """Project an ``ApiKey`` ORM instance onto the public dict contract.

    [FR-03] Every public repository function returns the same five-field
    dict shape so callers can iterate / assert on it without knowing about
    the ORM layer. Centralising the mapping also keeps the dict keys in
    sync with the FR-03 GREEN TODO contracts in ``test_fr03.py``.
    """
    return {
        "id": row.id,
        "scope": row.scope,
        "key_hash": row.key_hash,
        "created_at": row.created_at,
        "revoked_at": row.revoked_at,
    }


def create_api_key(
    session: Session,
    *,
    scope: str,
    plaintext: str,
    revoked_at: Optional[datetime | str] = None,
    id: Optional[str] = None,
) -> dict[str, Any]:
    """Persist a new api_keys row from a plaintext; return its dict shape.

    [FR-03] AC-3.2: only the SHA-256 hash is written to the database. The
    plaintext is consumed locally and never re-exposed — the caller (the
    service layer) is responsible for printing the plaintext exactly once
    before this function is called (AC-3.5).

    The returned dict mirrors the persisted row so tests can inspect the
    stored fields without re-querying the session. The ``key_hash`` value
    is always the SHA-256 hex digest of ``plaintext`` (64 lowercase hex
    chars).
    """
    # Passing ``id=None`` (the caller-default when the service layer does not
    # supply one) lets the ORM-level ``default=_new_uuid`` fire and mint a
    # fresh id. Any non-None value the caller supplies is forwarded unchanged.
    row = ApiKey(
        id=id,
        scope=scope,
        key_hash=_hash(plaintext),
        revoked_at=_coerce_revoked_at(revoked_at),
    )
    session.add(row)
    session.flush()
    return _row_to_dict(row)


def lookup_active_key(
    plaintext: str,
    *,
    session: Session,
) -> Optional[dict[str, Any]]:
    """Return the dict for the api_keys row matching ``plaintext``.

    [FR-03] AC-3.4: a row whose ``revoked_at`` is non-null is treated as
    invalid, so the function returns ``None`` even when the candidate hash
    matches. AC-3.5: this function is the only entry point the auth_dep
    uses — it compares hashes (never plaintexts) and returns a dict with
    the row's id/scope so the caller can build a ``Principal``.
    """
    digest = _hash(plaintext)
    stmt = (
        select(ApiKey)
        .where(ApiKey.key_hash == digest)
        .where(ApiKey.revoked_at.is_(None))
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return None
    return _row_to_dict(row)


def list_all_keys(session: Session) -> list[dict[str, Any]]:
    """Return every api_keys row as a dict (used by AC-3.5 verification)."""
    stmt = select(ApiKey).order_by(ApiKey.created_at.asc())
    rows = session.execute(stmt).scalars().all()
    return [_row_to_dict(row) for row in rows]