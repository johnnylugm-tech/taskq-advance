"""Repository functions for the ``rate_buckets`` table.

[FR-05] Owns every read and write of the per-key token-bucket row. The
bucket lives in the database (not in process memory) so every worker shares
one counter — a per-process bucket would let N workers each grant the full
burst, which is exactly the over-admission race R12 in SPEC.md §9.

AC-5.2: the refill-and-decrement is a read-modify-write on shared state, so
the row is selected with ``with_for_update()`` (``SELECT ... FOR UPDATE``)
and mutated inside the caller's single transaction. Holding the row lock for
the whole read-modify-write is what makes two concurrent consumers serialise
instead of both observing the same pre-decrement token count.

Note on SQLite: the SQLite dialect has no row-level lock and renders
``with_for_update()`` as a no-op — SQLite serialises writers at the database
level instead. The lock clause is therefore load-bearing only against the
production PostgreSQL target (SPEC.md §5.1).

Citations:
- SPEC.md#L115-L120 (FR-05 — token bucket in DB, single transaction, row-level lock)
- SPEC.md#L122-L128 (FR-06 — repository owns Session, no raw SQL)
- SAD.md#L144 (§2.4 `repository/rate_repo.py` — atomic refill under row-level lock)
- SAD.md#L256-L266 (§3.3 rate-limit flow)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from taskq_api.models.orm import RateBucket

__all__ = ["ConsumeResult", "consume_token"]

# [FR-05] What one admitted request costs. Named so the "can we afford it?"
# test and the debit below cannot drift apart, and so the bare ``1.0`` that
# used to appear in both places reads as a price rather than a magic number.
TOKEN_COST = 1.0


class ConsumeResult(NamedTuple):
    """Outcome of one ``consume_token`` attempt.

    [FR-05] ``allowed`` is ``True`` when a token was actually taken.
    ``tokens`` is the post-attempt token level: the remainder after the
    decrement when allowed, or the (unchanged, sub-token) level when
    rejected — the service layer turns the latter into ``Retry-After``.

    The repository returns data rather than raising an HTTP-shaped error
    because the repository layer may not import ``taskq_api.errors``
    (NFR-06 layering contract).
    """

    allowed: bool
    tokens: float


def _utcnow() -> datetime:
    """Return a timezone-aware UTC ``datetime``."""
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    """Coerce a possibly naive ``datetime`` read back from the DB to UTC.

    [FR-05] SQLite discards the timezone offset on round-trip, so a value
    written as aware comes back naive. Subtracting a naive from an aware
    ``datetime`` raises ``TypeError``, which would turn every second
    request into a 500. Normalising here keeps the elapsed-time maths
    correct on both SQLite and PostgreSQL.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _refilled_tokens(
    tokens: float,
    since: datetime,
    now: datetime,
    *,
    bucket_size: int,
    refill_rate_per_sec: float,
) -> float:
    """Token level after continuous refill from ``since`` to ``now``.

    [FR-05] Refill is continuous (``tokens + rate * elapsed``) and clamped at
    ``bucket_size`` so an idle key cannot bank more than one burst. Kept as a
    pure function of its arguments — no ORM row, no clock — so the clamp is
    readable on its own and ``consume_token`` below is left holding only the
    lock-and-mutate story.
    """
    elapsed_sec = (now - _as_aware(since)).total_seconds()
    return min(float(bucket_size), tokens + refill_rate_per_sec * elapsed_sec)


def consume_token(
    session: Session,
    key_id: str,
    *,
    bucket_size: int,
    refill_rate_per_sec: float,
) -> ConsumeResult:
    """Refill then take one token from ``key_id``'s bucket; report the outcome.

    [FR-05] AC-5.2: the bucket row is selected ``with_for_update()`` and
    mutated in the caller's transaction, so the refill-and-decrement is a
    single atomic step. The caller supplies the surrounding
    ``session_scope()``; committing here would release the row lock before
    the decision is final.

    A key with no bucket row yet starts at the full ``bucket_size`` — a new
    key is entitled to its whole burst. An existing row is advanced by
    ``_refilled_tokens`` before the debit is considered.
    """
    now = _utcnow()
    stmt = select(RateBucket).where(RateBucket.key_id == key_id).with_for_update()
    row = session.execute(stmt).scalar_one_or_none()

    if row is None:
        row = RateBucket(key_id=key_id, tokens=float(bucket_size), updated_at=now)
        session.add(row)
        # The session runs with ``autoflush=False``, so without this flush a
        # second consume in the same transaction would not see the row and
        # would queue a duplicate INSERT (an IntegrityError at commit).
        session.flush()
    else:
        row.tokens = _refilled_tokens(
            row.tokens,
            row.updated_at,
            now,
            bucket_size=bucket_size,
            refill_rate_per_sec=refill_rate_per_sec,
        )
        row.updated_at = now

    if row.tokens < TOKEN_COST:
        return ConsumeResult(allowed=False, tokens=row.tokens)

    row.tokens -= TOKEN_COST
    return ConsumeResult(allowed=True, tokens=row.tokens)
