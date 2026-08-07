"""taskq-api — HTTP task queue service.

[FR-01] Package root for the ASGI service started by
``uvicorn taskq_api.app:app``.

Citations:
- SPEC.md#L52-L57 (§1 概述 — ASGI service, `uvicorn taskq_api.app:app`)
- SPEC.md#L331-L365 (§6 資料夾結構 — package layout)
- SAD.md#L30-L66 (§2.1 Directory Structure)
"""

__all__ = ["__version__"]

__version__ = "1.0.0"
