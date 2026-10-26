"""Session auth routes (SESSION_AUTH_PLAN.md Piece B).

Cookie sessions for the UI: login issues an HttpOnly `soc_session` cookie and
returns a CSRF token; logout destroys the server-side session row; session
info echoes the actor for the UI to bootstrap from. Only active in
AUTH_MODE=session (the routes exist regardless so the doc-drift guard sees
them; flag-off the middleware never consults them).
"""
import logging
import os
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from api.auth import require_role
from pydantic import BaseModel

from api import auth
from api.helpers.audit import audit_event

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class DevLoginRequest(BaseModel):
    """Which existing account to mint a session for (SOC_DEV_LOGIN only)."""

    account: str


DEV_LOGIN_ENV = "SOC_DEV_LOGIN"


# ---------------------------------------------------------------------------
# F5: login throttling + constant-time user resolution.
#
# Every login attempt — valid user or not — runs a full password-KDF
# verification, so a valid username cannot be enumerated by timing
# (a missing/inactive user verifies against a dummy hash with the
# same cost as a real one). Failed attempts are counted on two
# independent sliding windows (per source IP and per attempted
# username); either window reaching LOGIN_MAX_FAILURES within
# LOGIN_WINDOW_SECONDS answers 429. Success clears both windows.
# ---------------------------------------------------------------------------
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
LOGIN_WINDOW_SECONDS = float(os.environ.get("LOGIN_WINDOW_SECONDS", "60"))

# ("ip", ip) / ("user", username) -> [failure timestamps]
_LOGIN_FAILURES: dict = {}
_DUMMY_HASH = None


def _dummy_password_hash() -> str:
    """Lazily built stand-in hash: same KDF, same parameters, so a
    missing-user login costs the same as a real one. Never verifies
    against any real password."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = auth.hash_password("soc-login-constant-time-dummy")
    return _DUMMY_HASH


def _login_windows(ip: str, username: str):
    now = time.time()
    keys = (("ip", ip), ("user", username))
    for key in keys:
        _LOGIN_FAILURES[key] = [
            t for t in _LOGIN_FAILURES.get(key, [])
            if now - t < LOGIN_WINDOW_SECONDS
        ]
    return keys


def _login_throttled(ip: str, username: str) -> bool:
    """True when either the per-IP or the per-username window is full."""
    for key in _login_windows(ip, username):
        if len(_LOGIN_FAILURES[key]) >= LOGIN_MAX_FAILURES:
            return True
    return False


def _record_login_failure(ip: str, username: str) -> None:
    now = time.time()
    for key in _login_windows(ip, username):
        _LOGIN_FAILURES[key].append(now)


def _clear_login_failures(ip: str, username: str) -> None:
    _LOGIN_FAILURES.pop(("ip", ip), None)
    _LOGIN_FAILURES.pop(("user", username), None)


def _issue_session(response: Response, user) -> dict:
    from db.models import AuthSession, SessionLocal

    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    db = SessionLocal()
    try:
        row = AuthSession(
            token_hash=auth.hash_token(token),
            csrf_token=csrf,
            user_id=user.id,
            expires_at=auth.session_expiry(),
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    response.set_cookie(
        auth.SESSION_COOKIE, token,
        httponly=True, samesite="lax", secure=auth.secure_cookies(),
        max_age=auth.SESSION_TTL_HOURS * 3600,
    )
    return {"user": user.username, "role": user.role, "csrf_token": csrf}


@router.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    from db.models import AuthSession, SessionLocal, User
    from db.util import utcnow_naive

    client_ip = request.client.host if request.client else "?"
    if _login_throttled(client_ip, payload.username):
        # Task 3 audit stream: throttled attempts are security events too
        # (they are the observable signal of a brute-force attempt).
        audit_event(
            "login_throttled", request, level=logging.WARNING,
            username=payload.username, ip=client_ip, status=429,
            reason="throttled",
        )
        raise HTTPException(status_code=429, detail="Too many login attempts")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == payload.username).first()
        # F5: resolve the hash in constant time — a missing or inactive
        # user verifies against the dummy hash, so every attempt pays
        # one full KDF run and usernames cannot be enumerated by timing.
        active_user = user is not None and user.is_active
        encoded = user.password_hash if active_user else _dummy_password_hash()
        password_ok = auth.verify_password(payload.password, encoded)
        if not active_user or not password_ok:
            _record_login_failure(client_ip, payload.username)
            # Same reason for unknown/inactive user and wrong password:
            # never let the audit stream distinguish the two either.
            audit_event(
                "login_failure", request, level=logging.WARNING,
                username=payload.username, ip=client_ip, status=401,
                reason="invalid_credentials",
            )
            raise HTTPException(status_code=401, detail="Invalid credentials")
        _clear_login_failures(client_ip, payload.username)
        audit_event(
            "login_success", request,
            username=user.username, ip=client_ip, status=200,
            actor="session", actor_role=user.role,
        )
        # F12: vacuum expired sessions on each successful login — logins
        # are infrequent, so one indexed range delete per login bounds the
        # table without a scheduler (which this repo deliberately avoids).
        db.query(AuthSession).filter(
            AuthSession.expires_at <= utcnow_naive()
        ).delete(synchronize_session=False)
        db.commit()
        return _issue_session(response, user)
    finally:
        db.close()


@router.post("/api/auth/logout", dependencies=[Depends(require_role("analyst"))])
def logout(request: Request, response: Response):
    # Audit BEFORE destroy_session so the actor still resolves.
    try:
        actor = auth.resolve_actor(request)
        audit_event(
            "logout", request,
            actor=actor.kind or "unknown",
            actor_role=actor.role or None,
            username=getattr(actor.user, "username", None),
        )
    except Exception:  # audit must never break the logout itself
        audit_event("logout", request, actor="unknown")
    auth.destroy_session(request)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


def dev_login_enabled() -> bool:
    """True when SOC_DEV_LOGIN=1 (local one-click role switching).

    Read live, not at import, so a test and an operator can flip it without a
    code change. Off by default: with it off the endpoint below does not exist
    as far as a caller can tell (404).
    """
    return os.environ.get(DEV_LOGIN_ENV, "").strip() == "1"


@router.post("/api/auth/dev-login")
def dev_login(payload: DevLoginRequest, request: Request, response: Response):
    """Mint a real session for `payload.account` — local convenience only.

    Why this exists: the human-facing login needs a password, and the operator
    should not have to remember one. The browser already holds the API key (the
    served page carries SOC_CONFIG.apiKey), and that key IS the admin machine
    actor — so minting a *lesser* session from it grants nothing new, it only
    lets the operator see the analyst experience (admin routes then 403).

    Guards, all of them fail-closed:
      * SOC_DEV_LOGIN=1 or the endpoint is 404 (indistinguishable from absent)
      * the armed API key must be presented — an analyst session cannot call
        its way up to admin here, and nothing works when API_KEY is unset
      * the account must exist and be active
    The password is never involved: accounts still have random passwords in the
    keychain for a real `POST /api/auth/login`.
    """
    if not dev_login_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    if not auth._API_KEY or not auth.request_has_api_key(request):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    from db.models import AuthSession, SessionLocal, User
    from db.util import utcnow_naive

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == payload.account).first()
        if user is None or not user.is_active:
            audit_event(
                "login_failure", request, level=logging.WARNING,
                username=payload.account, status=401,
                ip=request.client.host if request.client else "?",
                reason="dev_login_unknown_account",
            )
            raise HTTPException(status_code=401, detail="Unknown or inactive account")
        audit_event(
            "login_success", request,
            username=user.username, status=200,
            ip=request.client.host if request.client else "?",
            actor="session", actor_role=user.role,
            reason="dev_login",
        )
        db.query(AuthSession).filter(
            AuthSession.expires_at <= utcnow_naive()
        ).delete(synchronize_session=False)
        db.commit()
        return _issue_session(response, user)
    finally:
        db.close()


@router.get("/api/auth/session")
def session_info(request: Request):
    """Actor bootstrap for the UI.

    AUTH_MODE=session: 200 {user, role, csrf_token} for a live session. The
    deployment API key also answers 200 here (user "api-key", role admin): the
    browser carries that key, and without this branch a key-only deployment —
    every deployment whose accounts live elsewhere — got 401 and fell into a
    login modal that could never succeed. Anonymous: 401. AUTH_MODE=proxy: 200
    {mode:'proxy', user, role} when the trusted header resolved an account (the
    proxy owns login/logout, so the UI shows no modal and sends no CSRF token),
    401 otherwise. Flag off: 200 {mode:'api-key', role:'admin'} — auth is inert
    and the UI keeps admin controls enabled exactly like today; the constant
    shape lets app.modular.js branch on `mode` without feature-detecting.
    """
    actor = auth.resolve_actor(request)
    if auth.proxy_mode():
        if actor.kind == "anonymous":
            raise HTTPException(status_code=401, detail="Not authenticated")
        return {
            "mode": "proxy",
            "user": getattr(actor.user, "username", ""),
            "role": actor.role,
            # Header auth has no cookie, so there is no double-submit token.
            "csrf_token": "",
        }
    if not auth.session_mode():
        return {"mode": "api-key", "role": "admin", "user": "", "csrf_token": ""}
    if actor.kind == "api-key":
        return {
            "mode": "session",
            "user": "api-key",
            "role": "admin",
            # The key needs no double-submit token; the UI simply sends none.
            "csrf_token": "",
            "dev_login": dev_login_enabled(),
        }
    if actor.kind != "session":
        raise HTTPException(status_code=401, detail="Not signed in")
    return {
        "mode": "session",
        "user": actor.user.username,
        "role": actor.role,
        "csrf_token": actor.csrf_token,
        "dev_login": dev_login_enabled(),
    }
