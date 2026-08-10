"""Integration tests for taskq_api (NFR-10).

Run the live ASGI application through ``httpx.ASGITransport`` to exercise
the full middleware/auth/rate-limit stack end-to-end. The unit tests in
``test_frNN.py`` cover individual modules; this suite exists to make the
HTTP wiring regression-resistant.
"""

from __future__ import annotations
