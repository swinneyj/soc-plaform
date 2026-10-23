"""SEC-01 — AUTH_MODE=proxy header authentication.

The mode trusts one identity header, but only from a peer inside
SOC_TRUSTED_PROXY_CIDRS, and only when its value maps to an ACTIVE local
users row. These tests pin both halves: the happy path, every rejection
reason, and — the important ones — that an off-proxy caller cannot assert an
identity by sending the header, and that switching the flag does not change
the API-key or session modes at all.

TestClient's peer address is configurable (``client=(host, port)``), which is
what makes the trust-boundary tests real: the same header is sent from an
inside-CIDR peer and an outside one, and only the first may authenticate.
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

TRUSTED_PEER = ("10.9.8.7", 41000)
UNTRUSTED_PEER = ("192.0.2.55", 41000)
TRUSTED_CIDRS = "10.0.0.0/8,127.0.0.1"
HEADER = "X-Auth-Request-User"

ANALYST_ID = 1
ADMIN_ID = 2
INACTIVE_ID = 3


@pytest.fixture()
def db(monkeypatch):
    """In-memory SQLite with three users, repointed at db.models.SessionLocal.

    Routes import SessionLocal inside their handlers (the platform convention),
    so patching the module attribute is enough — no Postgres, no shared data.
    """
    import db.models as db_models

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", session)

    rows = [
        db_models.User(
            id=ANALYST_ID, username="analyst1", password_hash="scrypt$16384$8$1$00$00",
            role="analyst", is_active=True,
        ),
        db_models.User(
            id=ADMIN_ID, username="admin1", password_hash="scrypt$16384$8$1$00$00",
            role="admin", is_active=True,
        ),
        db_models.User(
            id=INACTIVE_ID, username="retired", password_hash="scrypt$16384$8$1$00$00",
            role="admin", is_active=False,
        ),
    ]
    db = session()
    try:
        db.add_all(rows)
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
def proxy(monkeypatch):
    """AUTH_MODE=proxy armed, trusted CIDR list set, no API key."""
    monkeypatch.setattr(api_auth, "_PROXY_MODE", True)
    monkeypatch.setattr(api_auth, "_API_KEY", "")
    monkeypatch.setenv("SOC_TRUSTED_PROXY_CIDRS", TRUSTED_CIDRS)
    return monkeypatch


def _client(peer=TRUSTED_PEER) -> TestClient:
    return TestClient(api_main.app, client=peer)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_trusted_header_authenticates_reads(db, proxy):
    client = _client()
    resp = client.get("/api/db/triage", headers={HEADER: str(ANALYST_ID)})
    assert resp.status_code == 200, resp.text


def test_session_endpoint_reports_proxy_mode(db, proxy):
    client = _client()
    resp = client.get("/api/auth/session", headers={HEADER: str(ADMIN_ID)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "proxy"
    assert body["user"] == "admin1"
    assert body["role"] == "admin"
    # No cookie, so no double-submit token to hand the UI.
    assert body["csrf_token"] == ""


def test_header_name_is_configurable(db, proxy):
    proxy.setenv("SOC_PROXY_USER_HEADER", "X-Forwarded-User")
    client = _client()
    assert client.get("/api/db/triage", headers={HEADER: str(ANALYST_ID)}).status_code == 401
    ok = client.get("/api/db/triage", headers={"X-Forwarded-User": str(ANALYST_ID)})
    assert ok.status_code == 200, ok.text


# ---------------------------------------------------------------------------
# Every rejection reason: missing, malformed, unknown, inactive
# ---------------------------------------------------------------------------

def test_missing_header_is_401(db, proxy):
    resp = _client().get("/api/db/triage")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_non_numeric_header_is_401(db, proxy):
    """The proxy may only name a user id — never a username or free text."""
    client = _client()
    for value in ("analyst1", "1 OR 1=1", "0x10", "  1x  ", "1,2"):
        resp = client.get("/api/db/triage", headers={HEADER: value})
        assert resp.status_code == 401, (value, resp.status_code)


def test_empty_header_is_401(db, proxy):
    resp = _client().get("/api/db/triage", headers={HEADER: ""})
    assert resp.status_code == 401


def test_unmapped_user_id_is_401(db, proxy):
    resp = _client().get("/api/db/triage", headers={HEADER: "999999"})
    assert resp.status_code == 401


def test_inactive_user_is_401(db, proxy):
    resp = _client().get("/api/db/triage", headers={HEADER: str(INACTIVE_ID)})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# The trust boundary — the reason this mode is safe to ship
# ---------------------------------------------------------------------------

def test_header_from_untrusted_peer_is_ignored(db, proxy):
    """A valid, existing user id from outside the trusted ranges is anonymous.

    This is the negative control for the whole design: without the CIDR check,
    any caller could assert any identity with one header.
    """
    resp = _client(UNTRUSTED_PEER).get("/api/db/triage", headers={HEADER: str(ADMIN_ID)})
    assert resp.status_code == 401


def test_no_cidr_configured_trusts_nobody(db, proxy):
    """Fail closed: an unconfigured or empty list authenticates no one."""
    proxy.delenv("SOC_TRUSTED_PROXY_CIDRS", raising=False)
    resp = _client().get("/api/db/triage", headers={HEADER: str(ANALYST_ID)})
    assert resp.status_code == 401


def test_malformed_cidr_entries_do_not_widen_trust(db, proxy):
    proxy.setenv("SOC_TRUSTED_PROXY_CIDRS", "not-a-cidr, ,also/bad")
    resp = _client().get("/api/db/triage", headers={HEADER: str(ANALYST_ID)})
    assert resp.status_code == 401


def test_bare_ip_entry_matches_that_peer_only(db, proxy):
    """A bare IP is treated as a /32 — it trusts one address, not the subnet."""
    proxy.setenv("SOC_TRUSTED_PROXY_CIDRS", "10.9.8.7")
    assert _client(("10.9.8.7", 41000)).get(
        "/api/db/triage", headers={HEADER: str(ANALYST_ID)}
    ).status_code == 200
    assert _client(("10.9.8.8", 41000)).get(
        "/api/db/triage", headers={HEADER: str(ANALYST_ID)}
    ).status_code == 401


# ---------------------------------------------------------------------------
# Roles come from the local users row
# ---------------------------------------------------------------------------

def test_proxy_actor_carries_role_analyst_forbidden_on_admin_route(db, proxy):
    resp = _client().get("/api/tools", headers={HEADER: str(ANALYST_ID)})
    assert resp.status_code == 403


def test_proxy_actor_carries_role_admin_allowed_on_admin_route(db, proxy):
    resp = _client().get("/api/tools", headers={HEADER: str(ADMIN_ID)})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# The other modes are untouched
# ---------------------------------------------------------------------------

def test_api_key_still_authenticates_in_proxy_mode(db, proxy):
    """Automation behind no proxy keeps working with the machine credential."""
    proxy.setattr(api_auth, "_API_KEY", "machine-secret")
    resp = _client(UNTRUSTED_PEER).get(
        "/api/db/triage", headers={"X-API-Key": "machine-secret"})
    assert resp.status_code == 200, resp.text


def test_flag_off_leaves_api_key_mode_unchanged(db, monkeypatch):
    """AUTH_MODE unset: reads are open, the proxy header means nothing."""
    monkeypatch.setattr(api_auth, "_PROXY_MODE", False)
    monkeypatch.setattr(api_auth, "_SESSION_MODE", False)
    monkeypatch.setattr(api_auth, "_API_KEY", "")
    monkeypatch.setenv("SOC_TRUSTED_PROXY_CIDRS", TRUSTED_CIDRS)
    client = _client(UNTRUSTED_PEER)
    assert client.get("/api/db/triage").status_code == 200
    # And a header cannot promote an anonymous caller into a role check.
    assert client.get("/api/tools").status_code != 403


def test_session_mode_ignores_the_proxy_header(db, monkeypatch):
    """Mode isolation: AUTH_MODE=session never reads the header (no privilege hop)."""
    monkeypatch.setattr(api_auth, "_SESSION_MODE", True)
    monkeypatch.setattr(api_auth, "_PROXY_MODE", False)
    monkeypatch.setattr(api_auth, "_API_KEY", "")
    monkeypatch.setenv("SOC_TRUSTED_PROXY_CIDRS", TRUSTED_CIDRS)
    resp = _client().get("/api/db/triage", headers={HEADER: str(ADMIN_ID)})
    assert resp.status_code == 401
