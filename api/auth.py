"""Shared API-key auth seam for the API surface.

Single module-level ``_API_KEY``, read live by both the mutation-gate
middleware (api.main) and the ``require_api_key`` FastAPI dependency used on
dangerous routes — tests patch exactly one point (``api.auth._API_KEY``).

Setting API_KEY activates gating; leaving it unset keeps local development
friction free (localhost-only exposure). The frontend stamps X-API-Key on
every axios request (web/utils/auth.js), and ``?api_key=`` is accepted as a
query parameter for smoke tests.
"""
import os
from typing import Optional

from fastapi import Header, HTTPException, Query

_API_KEY = os.environ.get("API_KEY", "").strip()


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    api_key: Optional[str] = Query(default=None),
) -> None:
    """FastAPI dependency: reject requests unless they present the API key.

    Accepts the key as either the X-API-Key header or an ?api_key= query
    parameter — either one passes. When API_KEY is unset the dependency is a
    no-op so local dev and existing tests keep working unchanged.
    """
    if not _API_KEY:
        return
    if x_api_key == _API_KEY or api_key == _API_KEY:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


def mutation_gate_rejects(request) -> bool:
    """True when the middleware must reject this request.

    Every mutating /api/* request (POST/PUT/PATCH/DELETE) is gated when
    API_KEY is armed. Read-only GETs, /api/health (deploy smoke test), and
    the static UI stay open per the contract in api.main.
    """
    return bool(
        _API_KEY
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.url.path.startswith("/api/")
    )


def request_has_api_key(request) -> bool:
    """True when the request presents the armed API key (header or query)."""
    header = request.headers.get("X-API-Key")
    query = request.query_params.get("api_key")
    return header == _API_KEY or query == _API_KEY
