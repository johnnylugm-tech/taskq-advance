"""Per-key token-bucket rate limiting.

[FR-05] Turns the repository's bucket arithmetic into the HTTP-facing
decision: either the request proceeds, or it is rejected with 429 +
``Retry-After`` (AC-5.1). The limit is per API key ("per-token"), so one
noisy key cannot spend another key's budget.

The bucket capacity and refill rate come from ``TASKQ_RATE_BURST`` /
``TASKQ_RATE_PER_SEC`` and are read per call, so an operator restart is not
needed to change them and the test suite can drive the boundary directly.

Citations:
- SPEC.md#L115-L120 (FR-05 — 令牌桶, 429 + Retry-After, healthz/readyz 不受限)
- SPEC.md#L296-L297 (§5.1 — TASKQ_RATE_BURST / TASKQ_RATE_PER_SEC)
- SAD.md#L157 (§2.4 `service/ratelimit.py` — per-key bucket, RFC 7807 429)
- SAD.md#L256-L266 (§3.3 rate-limit flow)
"""

from __future__ import annotations

import math

from taskq_api.config import rate_burst, rate_per_sec
from taskq_api.errors import RateLimitedError
from taskq_api.repository import rate_repo
from taskq_api.repository.session import session_scope

__all__ = ["RATE_LIMITED_DETAIL", "consume"]

# [FR-05 / NFR-04] The detail stays generic: it states the condition without
# echoing the key id, the bucket level, or any other caller-identifying state.
RATE_LIMITED_DETAIL = "rate limit exceeded"


def _retry_after_seconds(tokens: float, refill_rate_per_sec: float) -> int:
    """Whole seconds until the bucket can afford one request again (min 1).

    [FR-05] AC-5.1: ``Retry-After`` is expressed in seconds, so the fractional
    wait ``deficit / rate`` is rounded UP — rounding down would invite the
    caller back before a token exists and produce a second 429.
    """
    deficit = rate_repo.TOKEN_COST - tokens
    return max(1, math.ceil(deficit / refill_rate_per_sec))


def consume(key_id: str) -> None:
    """Take one token for ``key_id``; raise ``RateLimitedError`` when empty.

    [FR-05] AC-5.1 / AC-5.2: the refill-and-decrement runs inside a single
    ``session_scope()`` transaction against the DB-persisted bucket, so
    concurrent workers share one counter.

    The rejection is raised AFTER the transaction closes on purpose: raising
    inside the context manager would roll the transaction back, discarding the
    refill timestamp, and the bucket would never advance for a caller that
    keeps hitting the limit.
    """
    burst = rate_burst()
    per_sec = rate_per_sec()

    with session_scope() as session:
        outcome = rate_repo.consume_token(
            session, key_id, bucket_size=burst, refill_rate_per_sec=per_sec
        )

    if not outcome.allowed:
        raise RateLimitedError(
            RATE_LIMITED_DETAIL,
            retry_after=_retry_after_seconds(outcome.tokens, per_sec),
        )
