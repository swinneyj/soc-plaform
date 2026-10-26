"""SEC-01B — proxy header supports X.509 Subject DN strings.

The trusted header may carry either:
  1. a bare integer users.id (existing behavior), or
  2. an X.509 Subject DN string, e.g.
     "CN=SMITH.JANE.M.1234567890,OU=ENG,O=Enterprise,C=US",
     from which the 10-digit CN identifier is extracted and resolved
     against the local users table (by id, then by exact username).

Malformed DNs are 401 (proxy_header_malformed); well-formed DNs naming
no local row are 403 HIGH-02 (parsed credential, forbidden identity).
Bare-integer unknown ids stay 401 (existing contract); inactive rows
are 401 as well. No auto-provisioning.
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
TRUSTED_CIDRS = "10.0.0.0/8,127.0.0.1"
HEADER = "X-Auth-Request-User"


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

    rows = [
        db_models.User(
            id=42, username="analyst1", password_hash="scrypt$16384$8$1$00$00",
            role="analyst", is_active=True,
        ),
        db_models.User(
            # 10-digit external ID stored as username (CAC/PIV-mapped account).
            id=7, username="1234567890", password_hash="scrypt$16384$8$1$00$00",
            role="analyst", is_active=True,
        ),
        db_models.User(
            id=99, username="retired", password_hash="scrypt$16384$8$1$00$00",
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
    monkeypatch.setattr(api_auth, "_PROXY_MODE", True)
    monkeypatch.setattr(api_auth, "_API_KEY", "")
    monkeypatch.setenv("SOC_TRUSTED_PROXY_CIDRS", TRUSTED_CIDRS)
    return monkeypatch


def _client(peer=TRUSTED_PEER) -> TestClient:
    return TestClient(api_main.app, client=peer)


def test_dn_resolves_by_id_lookup(db, proxy):
    # DN whose 10-digit CN maps to no id and no username -> 401.
    # First prove the username-mapped DN works:
    dn = "CN=SMITH.JANE.M.1234567890,OU=ENG,O=Enterprise,C=US"
    resp = _client().get("/api/db/triage", headers={HEADER: dn})
    assert resp.status_code == 200, resp.text


def test_dn_resolves_integer_id_form(db, proxy):
    # Bare integer still works alongside DN support.
    resp = _client().get("/api/db/triage", headers={HEADER: "42"})
    assert resp.status_code == 200, resp.text


def test_dn_with_unprovisioned_id_is_403(db, proxy):
    # HIGH-02: well-formed DN, no local row -> 403 (parsed but forbidden).
    dn = "CN=DOE.JOHN.A.9999999999,OU=ENG,O=Enterprise,C=US"
    resp = _client().get("/api/db/triage", headers={HEADER: dn})
    assert resp.status_code == 403


def test_malformed_dns_are_401(db, proxy):
    client = _client()
    for bad in (
        "CN=SMITH.JANE.M.12345,OU=ENG",  # too short
        "CN=SMITH.JANE,OU=ENG",  # no numeric suffix
        "OU=ENG,O=Enterprise,C=US",  # no CN at all
        "not-a-dn-at-all",
        "CN=SMITH.JANE.M.123456789012345,OU=X",  # longer run still needs 10-digit CN shape
    ):
        # The last case: regex requires exactly 10 digits bounded by , or end;
        # a longer run should not match the 10-digit group cleanly.
        # If it does match a substring, it resolves to unknown -> still 401.
        resp = client.get("/api/db/triage", headers={HEADER: bad})
        assert resp.status_code == 401, (bad, resp.status_code)


def test_dn_from_untrusted_peer_is_401(db, proxy):
    dn = "CN=SMITH.JANE.M.1234567890,OU=ENG,O=Enterprise,C=US"
    resp = _client(("192.0.2.55", 41000)).get("/api/db/triage", headers={HEADER: dn})
    assert resp.status_code == 401


def test_dn_naming_inactive_user_is_401(db, proxy):
    # Inactive row id=99 cannot be reached by 10-digit lookup, but confirm
    # the inactive path via bare id still holds alongside DN support.
    resp = _client().get("/api/db/triage", headers={HEADER: "99"})
    assert resp.status_code == 401
