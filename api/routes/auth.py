"""Session auth routes (SESSION_AUTH_PLAN.md Piece B).

Cookie sessions for the UI: login issues an HttpOnly `soc_session` cookie and
returns a CSRF token; logout destroys the server-side session row; session
info echoes the actor for the UI to bootstrap from. Only active in
AUTH_MODE=session (the routes exist regardless so the doc-drift guard sees
them; flag-off the middleware never consults them).
"""
import secrets

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from api import auth

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


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
def login(payload: LoginRequest, response: Response):
    from db.models import SessionLocal, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == payload.username).first()
        if not user or not user.is_active or not auth.verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
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
    actor = auth.resolve_actor(request)
    if actor.kind != "session":
        raise HTTPException(status_code=401, detail="Not signed in")
    return {"user": actor.user.username, "role": actor.role, "csrf_token": actor.csrf_token}
