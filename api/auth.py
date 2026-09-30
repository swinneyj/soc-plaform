"""Shared API-key auth seam for the API surface.

Single module-level ``_API_KEY``, read live by both the mutation-gate
middleware (api.main) and the ``require_api_key`` FastAPI dependency used on
dangerous routes — tests patch exactly one point (``api.auth._API_KEY``).

Setting API_KEY activates gating; leaving it unset keeps local development
friction free (localhost-only exposure). The frontend stamps X-API-Key on
every axios request (web/utils/auth.js), and ``?api_key=`` is accepted as a
query parameter for smoke tests.
"""
import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query

_API_KEY = os.environ.get("API_KEY", "").strip()

# Session mode (SESSION_AUTH_PLAN.md): AUTH_MODE=session activates cookie
# sessions + CSRF + roles. Unset/anything else keeps today's API-key behavior
# byte-identical. Read live via session_mode(); tests monkeypatch this module
# attribute.
_SESSION_MODE = os.environ.get("AUTH_MODE", "").strip().lower() == "session"


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

    Belt and suspenders (A1): GET requests to delete-shaped paths
    (*/delete, */batch-delete, */delete-all) are gated too, so a future
    GET mutation surface can never sail past the method-based gate.
    """
    path = request.url.path
    destructive_suffix = path.endswith(("/delete", "/batch-delete", "/delete-all"))
    return bool(
        _API_KEY
        and path.startswith("/api/")
        and (
            request.method in ("POST", "PUT", "PATCH", "DELETE")
            or (request.method == "GET" and destructive_suffix)
        )
    )


def request_has_api_key(request) -> bool:
    """True when the request presents the armed API key (header or query)."""
    header = request.headers.get("X-API-Key")
    query = request.query_params.get("api_key")
    return header == _API_KEY or query == _API_KEY


# ---------------------------------------------------------------------------
# Password hashing — stdlib KDF only (SESSION_AUTH_PLAN.md §4, Piece A).
# Preferred scheme is scrypt; some Python 3.9 builds lack OpenSSL scrypt
# support, so hash_password falls back to PBKDF2-HMAC-SHA256 (600k iterations,
# OWASP-recommended). verify_password dispatches on the stored scheme prefix,
# so hashes remain verifiable across runtimes.
# ---------------------------------------------------------------------------

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32
_PBKDF2_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    """Hash a password with scrypt when available, else PBKDF2-SHA256.

    Encodings: `scrypt$n$r$p$salthex$hashhex` or `pbkdf2$iterations$salthex$hashhex`.
    """
    salt = secrets.token_bytes(16)
    if hasattr(hashlib, "scrypt"):
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
        )
        return "scrypt$%d$%d$%d$%s$%s" % (_SCRYPT_N, _SCRYPT_R, _SCRYPT_P, salt.hex(), digest.hex())
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_SCRYPT_DKLEN,
    )
    return "pbkdf2$%d$%s$%s" % (_PBKDF2_ITERATIONS, salt.hex(), digest.hex())


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against a hash_password() encoding.

    Dispatches on the scheme prefix so scrypt- and pbkdf2-encoded hashes can
    coexist (e.g. after a runtime switch). A scrypt hash presented to a
    runtime without scrypt fails closed (False), not error.
    """
    try:
        parts = encoded.split("$")
        if parts[0] == "scrypt" and len(parts) == 6 and hasattr(hashlib, "scrypt"):
            _, n, r, p, salthex, hashhex = parts
            digest = hashlib.scrypt(
                password.encode("utf-8"), salt=bytes.fromhex(salthex),
                n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(hashhex)),
            )
            return hmac.compare_digest(digest.hex(), hashhex)
        if parts[0] == "pbkdf2" and len(parts) == 4:
            _, iters, salthex, hashhex = parts
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"),
                bytes.fromhex(salthex), int(iters), dklen=len(bytes.fromhex(hashhex)),
            )
            return hmac.compare_digest(digest.hex(), hashhex)
        return False
    except (ValueError, TypeError):
        return False
