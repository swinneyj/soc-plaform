"""SOC_DEV_LOGIN — one-click role switching without typing a password.

The local operator needs to see both the admin and the analyst experience, and
the browser already holds the API key (the served page carries
`SOC_CONFIG.apiKey`). This endpoint turns that key into a real session for an
existing account, so switching is a click.

What these tests pin, in order of importance:

* **it cannot be a privilege escalation** — the reverse direction is impossible:
  an analyst session cannot call it (no key), and it only ever mints sessions
  for accounts that already exist and are active;
* **it is off unless enabled** — with SOC_DEV_LOGIN unset the route answers 404
  exactly like a route that does not exist, even for a key holder;
* **the session it mints is real** — the role gate applies to it (analyst gets
  403 on an admin route, admin gets 200);
* **the key still bootstraps the UI** — `GET /api/auth/session` answers 200 for
  a key-only caller instead of 401 (the bug that left the UI stuck on a login
  modal with no account that could ever sign in).
"""

import os

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.auth as api_auth
import api.main as api_main

API_KEY = "machine-key-for-tests"
ADMIN = "soc-admin"
ANALYST = "soc-analyst"
INACTIVE = "soc-retired"


@pytest.fixture()
def db(monkeypatch):
    import db.models as db_models

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", session)

    dummy = api_auth.hash_password("not-used-by-these-tests")
    db = session()
    try:
        db.add_all([
            db_models.User(id=1, username=ADMIN, password_hash=dummy, role="admin", is_active=True),
            db_models.User(id=2, username=ANALYST, password_hash=dummy, role="analyst", is_active=True),
            db_models.User(id=3, username=INACTIVE, password_hash=dummy, role="admin", is_active=False),
        ])
        db.commit()
    finally:
        db.close()

    api_main._RATE_HITS.clear()
    api_auth._actor_cache.clear()
    try:
        yield session
    finally:
        engine.dispose()


@pytest.fixture()
def session_mode(monkeypatch):
    """AUTH_MODE=session with the API key armed — the deployment this Mac runs."""
    monkeypatch.setattr(api_auth, "_SESSION_MODE", True)
    monkeypatch.setattr(api_auth, "_PROXY_MODE", False)
    monkeypatch.setattr(api_auth, "_API_KEY", API_KEY)
    monkeypatch.delenv("SOC_DEV_LOGIN", raising=False)
    return monkeypatch


def _client() -> TestClient:
    return TestClient(api_main.app)


def _dev_login(client, account, key=API_KEY):
    headers = {"X-API-Key": key} if key else {}
    return client.post("/api/auth/dev-login", json={"account": account}, headers=headers)


# ---------------------------------------------------------------------------
# Off by default
# ---------------------------------------------------------------------------

def test_disabled_flag_is_indistinguishable_from_absent(db, session_mode):
    resp = _dev_login(_client(), ANALYST)
    assert resp.status_code == 404, resp.text


def test_enabled_flag_advertised_in_the_bootstrap(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    body = _client().get("/api/auth/session", headers={"X-API-Key": API_KEY}).json()
    assert body["dev_login"] is True


def test_bootstrap_reports_dev_login_false_when_disabled(db, session_mode):
    body = _client().get("/api/auth/session", headers={"X-API-Key": API_KEY}).json()
    assert body["dev_login"] is False


# ---------------------------------------------------------------------------
# It cannot be reached without the machine credential
# ---------------------------------------------------------------------------

def test_requires_the_api_key(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    assert _dev_login(_client(), ANALYST, key=None).status_code == 401
    assert _dev_login(_client(), ANALYST, key="wrong-key").status_code == 401


def test_refuses_when_machine_key_is_not_armed(db, session_mode):
    """No armed key means no machine credential means no minting, flag or not."""
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    session_mode.setattr(api_auth, "_API_KEY", "")
    assert _dev_login(_client(), ANALYST, key=None).status_code == 401


# ---------------------------------------------------------------------------
# Unknown / inactive accounts
# ---------------------------------------------------------------------------

def test_unknown_account_is_rejected(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    resp = _dev_login(_client(), "no-such-account")
    assert resp.status_code == 401


def test_inactive_account_is_rejected(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    assert _dev_login(_client(), INACTIVE).status_code == 401


# ---------------------------------------------------------------------------
# The minted session is a real session
# ---------------------------------------------------------------------------

def test_analyst_session_has_the_analyst_role(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    client = _client()
    resp = _dev_login(client, ANALYST)
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"] == ANALYST
    assert resp.json()["role"] == "analyst"

    info = client.get("/api/auth/session")  # cookie, no key
    assert info.status_code == 200
    assert info.json()["user"] == ANALYST
    assert info.json()["role"] == "analyst"

    # Role gate applies: admin-only route refuses this session (SEC-02 Q3:
    # /api/tools is analyst, so use the still-admin registry).
    assert client.get("/api/registry").status_code == 403


def test_admin_session_clears_the_admin_gate(db, session_mode):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    client = _client()
    assert _dev_login(client, ADMIN).status_code == 200
    assert client.get("/api/auth/session").json()["role"] == "admin"
    assert client.get("/api/registry").status_code == 200


def test_switching_back_and_forth_replaces_the_session(db, session_mode):
    """Same browser, two sessions: the cookie is replaced, not layered."""
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    client = _client()
    assert _dev_login(client, ANALYST).status_code == 200
    assert client.get("/api/registry").status_code == 403
    assert _dev_login(client, ADMIN).status_code == 200
    assert client.get("/api/registry").status_code == 200


# ---------------------------------------------------------------------------
# The bootstrap bug this fixes
# ---------------------------------------------------------------------------

def test_api_key_bootstraps_the_ui_in_session_mode(db, session_mode):
    """Was 401 → the UI showed a login modal that could never succeed."""
    resp = _client().get("/api/auth/session", headers={"X-API-Key": API_KEY})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "session"
    assert body["user"] == "api-key"
    assert body["role"] == "admin"


def test_anonymous_still_gets_401_from_the_bootstrap(db, session_mode):
    assert _client().get("/api/auth/session").status_code == 401


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_minted_session_is_audited_as_a_dev_login(db, session_mode, caplog):
    session_mode.setenv("SOC_DEV_LOGIN", "1")
    with caplog.at_level("DEBUG", logger="soc.audit"):
        assert _dev_login(_client(), ANALYST).status_code == 200
    # audit_event logs `extra={"audit": payload}`, so the allowlisted fields
    # ride on the record as one dict (the JSON formatter is what writes them to
    # stdout).
    payloads = [getattr(record, "audit", {}) for record in caplog.records]
    assert any(p.get("reason") == "dev_login" for p in payloads)
    assert any(p.get("event") == "login_success" and p.get("username") == ANALYST
               for p in payloads)
    # No password material, ever.
    assert not any("password" in str(p) for p in payloads)
