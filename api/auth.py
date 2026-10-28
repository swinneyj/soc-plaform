"""Shared API-key auth seam for the API surface.

Single module-level ``_API_KEY``, read live by both the mutation-gate
middleware (api.main) and the ``require_api_key`` FastAPI dependency used on
dangerous routes — tests patch exactly one point (``api.auth._API_KEY``).

Setting API_KEY activates gating; leaving it unset keeps local development
friction free (localhost-only exposure). The frontend stamps X-API-Key on
every axios request (web/utils/auth.js). ``?api_key=`` is accepted as a
query parameter ONLY when AUTH_ALLOW_QUERY_KEY=1 is set (dev-only smoke
tests) — query-param credentials leak into access logs, browser history,
Referer headers and proxy logs, so the default is fail-closed (F2).
"""
import hashlib
import hmac
import ipaddress
import os
import re
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

# Proxy mode (SEC-01): AUTH_MODE=proxy trusts an identity header stamped by a
# reverse proxy that has ALREADY authenticated the user (nginx auth_request,
# oauth2-proxy, Pomerium, an ALB with OIDC). Same fail-closed shape as session
# mode: every /api/* request needs a real actor, reads included. Inert unless
# the flag is set, so the API-key and session modes are byte-identical.
_PROXY_MODE = os.environ.get("AUTH_MODE", "").strip().lower() == "proxy"


def query_key_allowed() -> bool:
    """True only when AUTH_ALLOW_QUERY_KEY=1 (dev-only smoke tests).

    Read live (not at import) so tests can flip it with monkeypatch.setenv
    and operators can toggle it without a code change. Default off: F2."""
    return os.environ.get("AUTH_ALLOW_QUERY_KEY", "").strip() == "1"


def require_api_key(
    x_api_key: Optional[str] = Header(default=None),
    api_key: Optional[str] = Query(default=None),
) -> None:
    """FastAPI dependency: reject requests unless they present the API key.

    The X-API-Key header always passes. The ?api_key= query parameter
    passes only when query_key_allowed() (AUTH_ALLOW_QUERY_KEY=1) —
    otherwise it is ignored and a query-only request 401s. When API_KEY
    is unset the dependency is a no-op so local dev keeps working.
    """
    if not _API_KEY:
        return
    if x_api_key == _API_KEY:
        return
    if api_key == _API_KEY and query_key_allowed():
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
    """True when the request presents the armed API key.

    Header always counts; the ?api_key= query parameter counts only when
    query_key_allowed() (AUTH_ALLOW_QUERY_KEY=1). Mirrors require_api_key
    so the middleware gate and the dependency can never disagree (F2)."""
    if request.headers.get("X-API-Key") == _API_KEY:
        return True
    return request.query_params.get("api_key") == _API_KEY and query_key_allowed()


# ---------------------------------------------------------------------------
# Password hashing — PBKDF2-HMAC-SHA256 only (FIPS 140-3 approved KDF;
# scrypt is not FIPS-approved and has no fallback path here). 600k
# iterations per OWASP Password Storage. 16-byte random salt per password.
#
# Encoding: `pbkdf2$iterations$salthex$hashhex`. Legacy `scrypt$…` hashes
# (pre-FIPS scheme) fail closed in verify_password — affected accounts
# must reset via scripts/manage_users.py.
# ---------------------------------------------------------------------------

_PBKDF2_ITERATIONS = 600_000
_PBKDF2_DKLEN = 32


def hash_password(password: str) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256 (600k iterations)."""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_PBKDF2_DKLEN,
    )
    return "pbkdf2$%d$%s$%s" % (_PBKDF2_ITERATIONS, salt.hex(), digest.hex())


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of a password against a hash_password() encoding.

    PBKDF2 only. Anything else (including legacy `scrypt$…` hashes)
    fails closed (False), never errors.
    """
    try:
        parts = encoded.split("$")
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

# Optimization #1 (API security review): resolve_actor runs two DB
# queries (AuthSession + User) on EVERY /api/* request in session
# mode — the gate middleware resolves every request and the UI polls
# frequently. A short-TTL in-memory cache keyed by the session
# cookie's token HASH (never the cookie itself) spares those
# queries: the first request for a cookie pays the two queries,
# later ones within the TTL answer from the cache. Entries carry
# plain resolution values only — no ORM rows, no tokens — and are
# evicted on logout (destroy_session) and when the cached session
# expires. Staleness is bounded by ACTOR_CACHE_TTL: a role change
# or deactivation takes effect within one TTL (default 30 s, the
# review's 30–60 s band; 0 disables the cache entirely).
ACTOR_CACHE_TTL = float(os.environ.get("ACTOR_CACHE_TTL", "30"))
_actor_cache: dict = {}  # token_hash -> (expires_at, user_id, role, username, csrf_token)

# Plain-data stand-in for the ORM User on cache hits. Every current
# consumer of Actor.user reads .username (the session bootstrap
# route); id and role ride along for the common cases.
CachedUser = namedtuple("CachedUser", "id username role")

# Paths the gate never blocks. /api/auth/dev-login is here for CSRF, not for
# authorization: it carries its own armed-API-key check, and it must stay
# callable while a *session* cookie is present (switching roles in the UI), where
# the gate would otherwise demand a CSRF token for the switch itself.
EXEMPT_PATHS = (
    "/health", "/api/health", "/api/auth/login", "/api/auth/session",
    "/api/auth/dev-login",
)
SESSION_COOKIE = "soc_session"
SESSION_TTL_HOURS = 12


def session_mode() -> bool:
    """True when AUTH_MODE=session (cookie sessions + CSRF + roles active)."""
    return bool(_SESSION_MODE)


# ---------------------------------------------------------------------------
# Proxy mode (SEC-01, decided 2026-10-23) — header auth behind a trusted proxy.
#
# The proxy authenticates the human; this deployment trusts ONE header that the
# proxy stamps and maps its value to a local users.id. Two rules make that safe
# enough to ship:
#
#   1. The header is read ONLY when the TCP peer is inside
#      SOC_TRUSTED_PROXY_CIDRS. Anywhere else the header is ignored, not
#      honored-and-logged: an unauthenticated caller must never be able to
#      assert an identity by sending a header. No CIDR list configured means
#      nobody is trusted (fail closed) — a half-configured proxy locks people
#      out, which is the safe direction.
#   2. The value must be a plain integer that maps to an ACTIVE users row. No
#      username matching, no auto-provisioning: the proxy cannot mint accounts,
#      only name ones that already exist here.
#
# Residual CSRF note: proxy-header auth on a cookie-less request has no
# double-submit token, so a cross-site POST that reaches the proxy would carry
# the proxy's header too. That is the proxy's boundary to defend (strip inbound
# copies of the header, enforce Origin/SameSite) — K8S-03's ingress draft is
# where that lands. Recorded here so it is a decision, not an oversight.
# ---------------------------------------------------------------------------

PROXY_USER_HEADER_DEFAULT = "X-Auth-Request-User"
_PROXY_USER_ID_RE = re.compile(r"^[0-9]{1,18}$")
# SEC-01B: X.509 Subject DN support. Upstream proxies (oauth2-proxy,
# nginx auth_request, ALB) may forward the client certificate Subject DN
# instead of a bare integer, e.g.
# "CN=SMITH.JANE.M.1234567890,OU=ENG,O=Enterprise,C=US".
# The 10-digit numeric identifier is extracted from the CN segment and
# resolved against the local users table (by id, then by username match).
_PROXY_DN_CN_RE = re.compile(r"CN=[^,]+?\.(\d{10})(?:,|$)")


def proxy_mode() -> bool:
    """True when AUTH_MODE=proxy (trusted reverse-proxy identity header)."""
    return bool(_PROXY_MODE)


def proxy_header_name() -> str:
    """Header carrying the authenticated user id (default X-Auth-Request-User)."""
    return os.environ.get("SOC_PROXY_USER_HEADER", "").strip() or PROXY_USER_HEADER_DEFAULT


def trusted_proxy_networks() -> list:
    """Parsed SOC_TRUSTED_PROXY_CIDRS entries.

    Comma-separated IPs or CIDRs (``10.0.0.0/8``, ``127.0.0.1``). Parsed per
    call so operators can widen a trusted range without a restart and tests can
    set it with monkeypatch. Malformed entries are SKIPPED — they never widen
    trust to "anything".
    """
    networks = []
    for chunk in os.environ.get("SOC_TRUSTED_PROXY_CIDRS", "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            networks.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            continue
    return networks


def _remote_is_trusted_proxy(request) -> bool:
    """True when the TCP peer address falls inside a trusted proxy CIDR.

    Uses the peer address (``request.client``), never X-Forwarded-For: the
    forwarding chain is attacker-controlled until a trusted hop says otherwise,
    and the whole point of this check is to identify that hop. A non-IP peer
    (e.g. the TestClient default, ``testclient``) is not trusted.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    if not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(address in network for network in trusted_proxy_networks())


def _proxy_user(user_id: int):
    """Plain (id, username, role, is_active) for a users row, or None."""
    from db.models import SessionLocal, User

    db = SessionLocal()
    try:
        row = db.query(User).filter(User.id == user_id).first()
        if row is None:
            return None
        return (row.id, row.username, row.role, bool(row.is_active))
    finally:
        db.close()


def _proxy_user_by_username(username: str):
    """Plain (id, username, role, is_active) matched by exact username, or None."""
    from db.models import SessionLocal, User

    db = SessionLocal()
    try:
        row = db.query(User).filter(User.username == username).first()
        if row is None:
            return None
        return (row.id, row.username, row.role, bool(row.is_active))
    finally:
        db.close()


def _proxy_user_by_external_id(external_id: str):
    """Resolve a 10-digit DN identifier to a local users row.

    Order: integer users.id match first, then exact users.username match
    (operators may store the 10-digit ID as the username for CAC/PIV-mapped
    accounts). No auto-provisioning — unknown identifiers return None.
    """
    try:
        row = _proxy_user(int(external_id))
    except (ValueError, TypeError):
        row = None
    if row is not None:
        return row
    return _proxy_user_by_username(external_id)


def resolve_proxy_actor(request):
    """(Actor, reason) for AUTH_MODE=proxy.

    Success is ``(Actor(kind="proxy"), "")``. Failure returns an anonymous
    Actor plus the reason the gate should audit: ``proxy_header_missing``,
    ``proxy_untrusted``, ``proxy_header_malformed``, ``proxy_user_unknown`` or
    ``proxy_user_inactive``. HIGH-02: the middleware gate maps a DN-shaped
    ``proxy_user_unknown`` (raw header contains ``CN=``) to 403; every other
    failure stays 401. A valid API key still authenticates as the admin
    machine actor, so automation and health checks keep working on a
    proxy-gated deployment.
    """
    raw = request.headers.get(proxy_header_name())
    reason = ""
    if raw is None:
        reason = "proxy_header_missing"
    elif not _remote_is_trusted_proxy(request):
        # A peer outside the trusted ranges cannot assert an identity. The
        # header is discarded rather than reported in detail.
        reason = "proxy_untrusted"
    else:
        value = raw.strip()
        if _PROXY_USER_ID_RE.match(value):
            row = _proxy_user(int(value))
            if row is None:
                reason = "proxy_user_unknown"
            elif not row[3]:
                reason = "proxy_user_inactive"
            else:
                user = CachedUser(id=row[0], username=row[1], role=row[2])
                # csrf_ok: header auth carries no cookie, so there is nothing
                # to double-submit. See the residual CSRF note above.
                return Actor(kind="proxy", role=row[2], user=user, csrf_ok=True, csrf_token=""), ""
        else:
            # SEC-01B: X.509 Subject DN string. Extract the 10-digit
            # identifier from the CN segment; anything without that shape
            # is malformed (401). A well-formed DN naming no local row is
            # unknown (401 via the gate — the anonymous actor path below).
            match = _PROXY_DN_CN_RE.search(value)
            if not match:
                reason = "proxy_header_malformed"
            else:
                row = _proxy_user_by_external_id(match.group(1))
                if row is None:
                    reason = "proxy_user_unknown"
                elif not row[3]:
                    reason = "proxy_user_inactive"
                else:
                    user = CachedUser(id=row[0], username=row[1], role=row[2])
                    return Actor(kind="proxy", role=row[2], user=user, csrf_ok=True, csrf_token=""), ""
    if _API_KEY and request_has_api_key(request):
        return Actor(kind="api-key", role="admin", user=None, csrf_ok=True, csrf_token=""), ""
    return Actor(kind="anonymous", role="", user=None, csrf_ok=False, csrf_token=""), reason


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


def _csrf_ok(request, csrf_token: str) -> bool:
    """Double-submit check of the X-CSRF-Token header against the
    session's CSRF token."""
    header = request.headers.get("X-CSRF-Token")
    # nosemgrep: python.django.security.nan-injection.nan-injection -- header is a str (or None); bool() on a string can never produce NaN, and the compare is constant-time
    return bool(header) and hmac.compare_digest(
        header.encode("utf-8"), csrf_token.encode("utf-8")
    )


def resolve_actor(request) -> Actor:
    """Classify the request: session user, API-key machine actor, or anonymous.

    A valid API key is a deployment credential and authenticates as admin
    (SESSION_AUTH_PLAN §2); sessions need an unexpired AuthSession row for
    the cookie's token hash. Session resolution is served from the
    short-TTL _actor_cache (optimization #1): the first request for a
    cookie pays the two DB queries, subsequent ones within the TTL do
    not — the cached values include the session expiry, so an expired
    session is never served from the cache.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if proxy_mode():
        # Proxy deployments have no session cookie contract: the identity is
        # the trusted header (or the API key). Delegating here keeps the gate,
        # the audit stream and require_role reading ONE resolution path.
        return resolve_proxy_actor(request)[0]
    if token:
        token_hash = hash_token(token)
        if ACTOR_CACHE_TTL > 0:
            entry = _actor_cache.get(token_hash)
            if entry is not None:
                expires_at, user_id, role, username, csrf_token = entry
                from db.util import utcnow_naive

                if expires_at > utcnow_naive():
                    return Actor(
                        kind="session", role=role,
                        user=CachedUser(id=user_id, username=username, role=role),
                        csrf_ok=_csrf_ok(request, csrf_token),
                        csrf_token=csrf_token,
                    )
                # Cached session has expired: drop the entry and
                # re-resolve from the DB below.
                _actor_cache.pop(token_hash, None)
        session = _session_from_request(request)
        if session is not None:
            row, user = session
            if ACTOR_CACHE_TTL > 0:
                _actor_cache[token_hash] = (
                    row.expires_at, user.id, user.role, user.username, row.csrf_token,
                )
            return Actor(
                kind="session", role=user.role, user=user,
                csrf_ok=_csrf_ok(request, row.csrf_token),
                csrf_token=row.csrf_token,
            )
    if _API_KEY and request_has_api_key(request):
        return Actor(kind="api-key", role="admin", user=None, csrf_ok=True, csrf_token="")
    return Actor(kind="anonymous", role="", user=None, csrf_ok=False, csrf_token="")


def destroy_session(request) -> None:
    """Delete the AuthSession row for the request's cookie (logout)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    # Optimization #1: evict the cached resolution so a logged-out
    # cookie cannot ride out its TTL as an authenticated actor (the
    # gate middleware may have cached it earlier in this same request).
    _actor_cache.pop(hash_token(token), None)
    from db.models import AuthSession, SessionLocal

    db = SessionLocal()
    try:
        db.query(AuthSession).filter(AuthSession.token_hash == hash_token(token)).delete()
        db.commit()
    finally:
        db.close()


def require_role(role: str):
    """FastAPI dependency: require an authenticated actor with `role`.

    Binds in session AND proxy mode — both resolve a local users row, so both
    carry a real role. Flag-off this stays a no-op, which keeps every existing
    route and test byte-identical.

    Hierarchy (SEC-02): admin satisfies analyst (an admin can work the
    investigation flow); analyst never satisfies admin.
    """
    def _dependency(request: Request) -> None:
        if not (session_mode() or proxy_mode()):
            return
        actor = resolve_actor(request)
        if actor.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Authentication required")
        if actor.role == role:
            return
        # Admin inherits analyst (SEC-02 hierarchy); nothing else upgrades.
        if role == "analyst" and actor.role == "admin":
            return
        raise HTTPException(status_code=403, detail="Insufficient role")

    return _dependency
