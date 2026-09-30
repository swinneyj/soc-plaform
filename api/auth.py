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
from collections import namedtuple
from datetime import timedelta
from typing import Optional

from fastapi import Header, HTTPException, Query, Request

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
    """True when the request is a mutating or destructive /api/* call.

    Key-independent since C1B.2: this classifies the REQUEST; whether a key,
    a session, or nothing satisfies the gate is decided by the middleware
    branch in api.main based on AUTH_MODE. The destructive-suffix clause is
    the A1 path-aware belt-and-suspenders: even a hypothetical future GET
    delete-shaped route lands in the gate.
    """
    path = request.url.path
    destructive_suffix = path.endswith(("/delete", "/batch-delete", "/delete-all"))
    return bool(
        path.startswith("/api/")
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


# ---------------------------------------------------------------------------
# Session auth (SESSION_AUTH_PLAN.md) — active only in AUTH_MODE=session.
# DB access goes through in-function imports of db.models so tests can
# repoint SessionLocal the way test_api_analyze_flow does.
# ---------------------------------------------------------------------------

Actor = namedtuple("Actor", "kind role user csrf_ok csrf_token")

EXEMPT_PATHS = ("/health", "/api/health", "/api/auth/login", "/api/auth/session")
SESSION_COOKIE = "soc_session"
SESSION_TTL_HOURS = 12


def session_mode() -> bool:
    """True when AUTH_MODE=session (cookie sessions + CSRF + roles active)."""
    return bool(_SESSION_MODE)


def session_expiry():
    """Naive-UTC now + 12h (db.util is the api-side clock)."""
    from db.util import utcnow_naive

    return utcnow_naive() + timedelta(hours=SESSION_TTL_HOURS)


def secure_cookies() -> bool:
    """SESSION_SECURE=1 marks the session cookie Secure (set behind HTTPS)."""
    return os.environ.get("SESSION_SECURE", "").strip() in ("1", "true", "yes")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_from_request(request):
    """(AuthSession, User) for the request's cookie, or None if absent/expired."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    from db.models import AuthSession, SessionLocal, User
    from db.util import utcnow_naive

    db = SessionLocal()
    try:
        row = (
            db.query(AuthSession)
            .filter(AuthSession.token_hash == hash_token(token))
            .first()
        )
        if row is None or row.expires_at <= utcnow_naive():
            return None
        user = db.query(User).filter(User.id == row.user_id).first()
        if user is None or not user.is_active:
            return None
        return row, user
    finally:
        db.close()


def resolve_actor(request) -> Actor:
    """Classify the request: session user, API-key machine actor, or anonymous.

    A valid API key is a deployment credential and authenticates as admin
    (SESSION_AUTH_PLAN §2); sessions need an unexpired AuthSession row for
    the cookie's token hash.
    """
    session = _session_from_request(request)
    if session is not None:
        row, user = session
        header = request.headers.get("X-CSRF-Token")
        csrf_ok = bool(header) and hmac.compare_digest(
            header.encode("utf-8"), row.csrf_token.encode("utf-8")
        )
        return Actor(
            kind="session", role=user.role, user=user,
            csrf_ok=csrf_ok, csrf_token=row.csrf_token,
        )
    if _API_KEY and request_has_api_key(request):
        return Actor(kind="api-key", role="admin", user=None, csrf_ok=True, csrf_token="")
    return Actor(kind="anonymous", role="", user=None, csrf_ok=False, csrf_token="")


def destroy_session(request) -> None:
    """Delete the AuthSession row for the request's cookie (logout)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    from db.models import AuthSession, SessionLocal

    db = SessionLocal()
    try:
        db.query(AuthSession).filter(AuthSession.token_hash == hash_token(token)).delete()
        db.commit()
    finally:
        db.close()


def require_role(role: str):
    """FastAPI dependency: in session mode require an actor with `role`.

    Flag-off this is a no-op — roles only bind in AUTH_MODE=session — which
    keeps every existing route and test byte-identical until the flag flips.
    """
    def _dependency(request: Request) -> None:
        if not session_mode():
            return
        actor = resolve_actor(request)
        if actor.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Authentication required")
        if actor.role != role:
            raise HTTPException(status_code=403, detail="Insufficient role")

    return _dependency
