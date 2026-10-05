"""Session auth routes (SESSION_AUTH_PLAN.md Piece B).

Cookie sessions for the UI: login issues an HttpOnly `soc_session` cookie and
returns a CSRF token; logout destroys the server-side session row; session
info echoes the actor for the UI to bootstrap from. Only active in
AUTH_MODE=session (the routes exist regardless so the doc-drift guard sees
them; flag-off the middleware never consults them).
"""
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from api import auth

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


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
    from db.models import SessionLocal, User

    client_ip = request.client.host if request.client else "?"
    if _login_throttled(client_ip, payload.username):
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
            raise HTTPException(status_code=401, detail="Invalid credentials")
        _clear_login_failures(client_ip, payload.username)
        return _issue_session(response, user)
    finally:
        db.close()


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    auth.destroy_session(request)
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"ok": True}


@router.get("/api/auth/session")
def session_info(request: Request):
    """Actor bootstrap for the UI.

    AUTH_MODE=session: 200 {user, role, csrf_token} for a live session, 401
    otherwise (the UI shows the login modal). Flag off: 200 {mode:'api-key',
    role:'admin'} — auth is inert and the UI keeps admin controls enabled
    exactly like today; the constant shape lets app.modular.js branch on
    `mode` without feature-detecting.
    """
    actor = auth.resolve_actor(request)
    if not auth.session_mode():
        return {"mode": "api-key", "role": "admin", "user": "", "csrf_token": ""}
    if actor.kind != "session":
        raise HTTPException(status_code=401, detail="Not signed in")
    return {"mode": "session", "user": actor.user.username, "role": actor.role, "csrf_token": actor.csrf_token}
