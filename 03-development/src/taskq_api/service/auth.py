"""API-key authentication service (FR-03).

[FR-03] The thin service layer that sits between ``api.deps.auth_dep`` and
``repository.key_repo``. The two responsibilities are kept narrow on purpose:

* :func:`verify_key` performs a constant-time hash comparison so a brute-force
  attacker cannot mount a timing oracle (AC-3.3).
* :func:`create_api_key` is the one place where a plaintext API key is minted
  on behalf of the operator: it generates the plaintext, prints it exactly
  once via an injectable ``plaintext_writer``, and persists only its SHA-256
  hash (AC-3.5).

The service never invents a plaintext in production — that responsibility
belongs to the CLI / the operator. The function accepts an injectable
writer so the test harness can intercept stdout without depending on the
subprocess layer.

Citations:
- SPEC.md#L101-L107 (FR-03 — X-API-Key, hmac.compare_digest, revoked)
- SPEC.md#L166 (NFR-04 — no internal leakage in ``detail``)
- SAD.md#L146-L165 (§2.4 `service/auth.py` — auth operations)
- SRS.md (AC-3.3 / AC-3.5 — constant-time comparison, one-time print)
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Callable, Optional

from taskq_api.repository import key_repo

__all__ = ["verify_key", "create_api_key"]


# The plaintext prefix is the visible marker an operator scans for in the
# printed output. Keeping it constant lets the on-call rotate keys without
# having to inspect ``scope`` rows in the DB.
_PLAINTEXT_PREFIX = "sk-"


def verify_key(plaintext: str, stored_hash_hex: str) -> bool:
    """Return True iff ``plaintext`` hashes to ``stored_hash_hex``.

    [FR-03] AC-3.3: the comparison MUST go through ``hmac.compare_digest``
    so the equality check is constant-time. A naive ``==`` would leak the
    number of leading matching bytes via response-time analysis, which is
    exactly the timing oracle NFR-02 forbids.

    The candidate digest is computed with the same SHA-256 / UTF-8 encoding
    the repository uses, so a match implies the caller is in possession of
    the original plaintext.
    """
    candidate_digest = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return hmac.compare_digest(candidate_digest, stored_hash_hex)


def create_api_key(
    scope: str,
    *,
    session: Any,
    plaintext_writer: Callable[[str], None] = print,
    revoked_at=None,
    id: Optional[str] = None,
) -> dict:
    """Mint a new API key: print the plaintext once, persist only the hash.

    [FR-03] AC-3.5: the plaintext is emitted via ``plaintext_writer``
    exactly once (here, immediately before the database write) and never
    returned from this function or persisted in any form. The caller (CLI
    or test) decides where the plaintext lands — typically ``stdout`` for
    the operator to capture.

    The function generates a 32-byte URL-safe random plaintext via
    ``secrets.token_urlsafe`` and prefixes the conventional ``sk-`` marker
    so an operator scanning a terminal can recognise it as an API key.
    """
    plaintext = _PLAINTEXT_PREFIX + secrets.token_urlsafe(32)
    plaintext_writer(plaintext)
    return key_repo.create_api_key(
        session,
        scope=scope,
        plaintext=plaintext,
        revoked_at=revoked_at,
        id=id,
    )