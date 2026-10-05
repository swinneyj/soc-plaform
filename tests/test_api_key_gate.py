"""Tests for the API-key gate on dangerous routes.

The gate is a no-op unless API_KEY is set in the environment, so these tests
monkeypatch api.auth._API_KEY (the single auth seam) to exercise both modes.
"""

import os

# CORS middleware is mounted at api.main import time only when CORS_ORIGINS
# is set; set it here, before the import, so the CORS test below can run.
os.environ.setdefault("CORS_ORIGINS", "https://test.local")
CORS_TEST_ORIGIN = os.environ["CORS_ORIGINS"].split(",")[0].strip()

import pytest
from fastapi.testclient import TestClient

import api.auth as api_auth
import api.main as api_main


@pytest.fixture(autouse=True)
def isolated_boundary_staging(tmp_path, monkeypatch):
    """C4: route pastes now stage boundary batches — keep them out of the
    repo's Data/quarantine during tests."""
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(tmp_path / "quarantine"))


@pytest.fixture()
def client():
    api_main._RATE_HITS.clear()  # C2.1.5 limiter state is module-global
    return TestClient(api_main.app)


@pytest.fixture()
def with_api_key(monkeypatch):
    monkeypatch.setattr(api_auth, "_API_KEY", "test-secret-key")
    yield "test-secret-key"

def test_gate_is_noop_without_api_key(client):
    """Default dev mode: API_KEY unset, dangerous routes stay open."""
    resp = client.delete("/api/jobs/nonexistent-id")
    assert resp.status_code in (200, 404)  # 404 = reached handler, job missing


def test_cors_allows_x_api_key_header(client):
    """The browser sends X-API-Key; CORS must not silently strip it.

    Behavioral check: a real preflight requesting X-API-Key must be answered
    with that header in Access-Control-Allow-Headers.
    """
    resp = client.options(
        "/api/health",
        headers={
            "Origin": CORS_TEST_ORIGIN,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-api-key",
        },
    )
    assert resp.status_code in (200, 204)
    allow = resp.headers.get("access-control-allow-headers", "")
    assert "x-api-key" in allow.lower(), f"X-API-Key not allowed by CORS: {allow}"


def test_execute_rejected_without_key(client, with_api_key):
    resp = client.post("/api/execute", json={"tool_name": "anything"})
    assert resp.status_code == 401


def test_execute_rejected_with_wrong_key(client, with_api_key):
    resp = client.post(
        "/api/execute",
        json={"tool_name": "anything"},
        headers={"X-API-Key": "wrong"},
    )
    assert resp.status_code == 401


def test_execute_accepted_with_header_key(client, with_api_key):
    resp = client.post(
        "/api/execute",
        json={"tool_name": "definitely-not-a-real-tool"},
        headers={"X-API-Key": "test-secret-key"},
    )
    # Auth passed; handler runs and 404s on the unknown tool name.
    assert resp.status_code == 404


def test_execute_accepted_with_query_key(client, with_api_key):
    resp = client.post(
        "/api/execute?api_key=test-secret-key",
        json={"tool_name": "definitely-not-a-real-tool"},
    )
    assert resp.status_code == 404


def test_clear_jobs_rejected_without_key(client, with_api_key):
    resp = client.delete("/api/jobs")
    assert resp.status_code == 401


def test_regression_rejected_without_key(client, with_api_key):
    resp = client.post("/api/tools/regression")
    assert resp.status_code == 401


def test_health_stays_open_when_gated(client, with_api_key):
    """Read-only/health endpoints must remain reachable without a key."""
    resp = client.get("/api/health")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# C1B.1 — scrypt password hashing (SESSION_AUTH_PLAN.md Piece A)
# ---------------------------------------------------------------------------

def test_scrypt_roundtrip():
    encoded = api_auth.hash_password("correct horse battery staple")
    # scrypt is preferred; runtimes without OpenSSL scrypt fall back to pbkdf2.
    assert encoded.startswith(("scrypt$", "pbkdf2$"))
    assert api_auth.verify_password("correct horse battery staple", encoded)


def test_scrypt_wrong_password_fails():
    encoded = api_auth.hash_password("hunter2")
    assert not api_auth.verify_password("hunter3", encoded)
    assert not api_auth.verify_password("", encoded)
    assert not api_auth.verify_password("hunter2", "not-a-valid-encoding")


def test_password_never_stored_plaintext():
    encoded = api_auth.hash_password("swordfish")
    assert "swordfish" not in encoded
    # Random salt: the same password hashes differently every time.
    assert encoded != api_auth.hash_password("swordfish")


def test_pbkdf2_scheme_verify_path():
    """Exercise the pbkdf2 branch directly so both schemes verify on every
    runtime (C1B.1 fallback — some Python 3.9 builds lack hashlib.scrypt)."""
    import hashlib as _hashlib

    salt = bytes.fromhex("ab" * 16)
    digest = _hashlib.pbkdf2_hmac("sha256", b"pw123", salt, api_auth._PBKDF2_ITERATIONS, dklen=32)
    encoded = "pbkdf2$%d$%s$%s" % (api_auth._PBKDF2_ITERATIONS, salt.hex(), digest.hex())
    assert api_auth.verify_password("pw123", encoded)
    assert not api_auth.verify_password("pw124", encoded)


# ---------------------------------------------------------------------------
# C1B.2 — session auth routes + gates (SESSION_AUTH_PLAN.md Piece B)
# ---------------------------------------------------------------------------

@pytest.fixture()
def session_client(monkeypatch):
    """TestClient with AUTH_MODE=session semantics and an isolated SQLite DB.

    db.models.SessionLocal is repointed (same pattern as the analyze-flow
    tests) so the route handlers' in-function imports land in the throwaway
    database instead of the configured runtime DB.
    """
    import db.models as db_models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    monkeypatch.setattr(api_auth, "_SESSION_MODE", True)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", test_session)

    client = TestClient(api_main.app)
    client.test_session = test_session
    yield client
    engine.dispose()


def _make_user(db, username="alice", role="analyst", password="pw123"):
    from db.models import User

    user = User(username=username, role=role, password_hash=api_auth.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_login_ok_sets_cookie(session_client):
    db = session_client.test_session()
    _make_user(db, username="alice", password="pw123")
    resp = session_client.post("/api/auth/login", json={"username": "alice", "password": "pw123"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"] == "alice"
    assert body["role"] == "analyst"
    assert body["csrf_token"]
    assert "soc_session" in resp.cookies
    # The issued cookie resolves to the same actor on /api/auth/session.
    me = session_client.get("/api/auth/session")
    assert me.status_code == 200
    assert me.json()["user"] == "alice"
    assert me.json()["csrf_token"] == body["csrf_token"]


def test_login_bad_password_401(session_client):
    db = session_client.test_session()
    _make_user(db, username="bob", password="pw123")
    resp = session_client.post("/api/auth/login", json={"username": "bob", "password": "wrong"})
    assert resp.status_code == 401
    assert "soc_session" not in resp.cookies


def test_logout_invalidates(session_client):
    db = session_client.test_session()
    _make_user(db, username="carol", password="pw123")
    login = session_client.post("/api/auth/login", json={"username": "carol", "password": "pw123"})
    csrf = login.json()["csrf_token"]
    # Logout is itself a cookie-authed mutation, so it carries the CSRF header
    # exactly as web/utils/auth.js does for every mutation.
    out = session_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200
    assert session_client.get("/api/auth/session").status_code == 401


def test_csrf_missing_403(session_client):
    db = session_client.test_session()
    _make_user(db, username="dave", password="pw123")
    session_client.post("/api/auth/login", json={"username": "dave", "password": "pw123"})
    # A cookie-backed mutation without X-CSRF-Token must be rejected before
    # the handler runs (middleware-level CSRF gate).
    resp = session_client.post("/api/notables/paste", json={"raw_text": "x"})
    assert resp.status_code == 403
    assert "CSRF" in resp.json()["detail"]


def test_session_read_gate_401(session_client):
    """In session mode, non-exempt API reads require an authenticated actor."""
    resp = session_client.get("/api/db/stats")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Authentication required"


def test_session_info_flag_off_is_admin_bootstrap(client):
    """Flag-off byte-compat: session info answers 200 with the api-key mode
    marker so the UI keeps admin controls enabled exactly like pre-C1B."""
    resp = client.get("/api/auth/session")
    assert resp.status_code == 200
    assert resp.json() == {"mode": "api-key", "role": "admin", "user": "", "csrf_token": ""}


# ---------------------------------------------------------------------------
# C1B.4 — auth matrix: roles, API-key-as-admin, expiry (SESSION_AUTH_PLAN.md)
# ---------------------------------------------------------------------------

def test_role_gate_analyst_403(session_client):
    """An analyst session is authenticated but not admin: job deletion 403s."""
    db = session_client.test_session()
    _make_user(db, username="erin", role="analyst", password="pw123")
    login = session_client.post("/api/auth/login", json={"username": "erin", "password": "pw123"})
    csrf = login.json()["csrf_token"]
    resp = session_client.delete(
        "/api/jobs/some-job-id", headers={"X-CSRF-Token": csrf}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Insufficient role"


def test_api_key_still_admin_in_session_mode(session_client, monkeypatch):
    """The deployment credential authenticates as the admin machine actor in
    session mode — no cookie, no CSRF header needed (plan §2)."""
    monkeypatch.setattr(api_auth, "_API_KEY", "test-secret-key")
    # Read gate: the key satisfies it without any session.
    resp = session_client.get("/api/db/stats", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200
    # Mutation: admin machine actor needs no CSRF (that check is session-only).
    resp = session_client.post(
        "/api/notables/paste", json={"raw_text": "probe"},
        headers={"X-API-Key": "test-secret-key"},
    )
    assert resp.status_code not in (401, 403)


def test_expired_session_401(session_client):
    from datetime import timedelta

    from db.models import AuthSession
    from db.util import utcnow_naive

    db = session_client.test_session()
    _make_user(db, username="frank", password="pw123")
    session_client.post("/api/auth/login", json={"username": "frank", "password": "pw123"})
    row = db.query(AuthSession).first()
    row.expires_at = utcnow_naive() - timedelta(hours=1)
    db.commit()
    resp = session_client.get("/api/auth/session")
    assert resp.status_code == 401
    # And the read gate treats the expired cookie as anonymous.
    assert session_client.get("/api/db/stats").status_code == 401


# ---------------------------------------------------------------------------
# C2.1.x — route-level hardening gates (signed-off limits)
# ---------------------------------------------------------------------------

def _isolated_db_session(monkeypatch):
    """Override db.models.SessionLocal with an empty in-memory
    sqlite session so DB-touching admission paths stay hermetic."""
    import db.models as db_models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db_models.Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )
    monkeypatch.setattr(db_models, "SessionLocal", test_session)
    return engine, test_session


def test_job_queue_cap_429(client, with_api_key, monkeypatch):
    """C2.1.2: a full in-memory job queue rejects admission with 429 BEFORE
    registry lookup (unknown tool would 404; the cap must win)."""
    import api.routes.tools as tools_route

    _isolated_db_session(monkeypatch)
    monkeypatch.setattr(tools_route, "JOB_QUEUE_MAX", 2)
    seeded = [f"queued-{i}" for i in range(2)]
    for job_id in seeded:
        tools_route.jobs[job_id] = {
            "job_id": job_id, "status": "pending", "tool_name": "x",
            "created_at": "2026-09-30T00:00:00", "completed_at": None,
            "stdout": None, "stderr": None, "exit_code": None,
            "arguments": {}, "artifacts": [],
        }
    try:
        resp = client.post("/api/execute", json={"tool_name": "anything"},
                           headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 429
        assert "queue is full" in resp.json()["detail"]

        # One slot free -> admission proceeds (unknown tool then 404s).
        tools_route.jobs.pop(seeded[0])
        resp = client.post("/api/execute", json={"tool_name": "anything"},
                           headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 404
    finally:
        for job_id in seeded:
            tools_route.jobs.pop(job_id, None)


def test_job_queue_cap_429_counts_persisted_jobs(client, with_api_key, monkeypatch):
    """Regression: the C2.1.2 cap counted only the process-local
    jobs dict, so after a daemon restart (dict empty, persisted
    ToolRun rows still unfinished) admission silently reset to
    zero and accepted work past the cap."""
    import api.routes.tools as tools_route
    import db.models as db_models
    from db.util import utcnow_naive

    engine, test_session = _isolated_db_session(monkeypatch)

    # Simulate the post-restart state: empty in-memory queue,
    # one unfinished run persisted by the previous process.
    tools_route.jobs.clear()
    db = test_session()
    db.add(db_models.ToolRun(job_id="persisted-pending", tool_name="x",
                             status="pending", created_at=utcnow_naive()))
    db.commit()
    db.close()

    monkeypatch.setattr(tools_route, "JOB_QUEUE_MAX", 1)
    try:
        resp = client.post("/api/execute", json={"tool_name": "anything"},
                           headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 429
        assert "queue is full" in resp.json()["detail"]

        # A finished persisted row does NOT consume a slot.
        db = test_session()
        db.query(db_models.ToolRun).update({"status": "completed"})
        db.commit()
        db.close()
        resp = client.post("/api/execute", json={"tool_name": "anything"},
                           headers={"X-API-Key": "test-secret-key"})
        assert resp.status_code == 404  # admitted; unknown tool
    finally:
        tools_route.jobs.clear()
        engine.dispose()


def test_paste_payload_cap_413(client, monkeypatch):
    """C2.1.4: an oversized raw paste is rejected 413 before any parsing."""
    import api.routes.notables as notables_route

    monkeypatch.setattr(notables_route, "PASTE_MAX_BYTES", 10)
    resp = client.post("/api/notables/paste", json={"raw_text": "x" * 11})
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"]
    # At the cap the gate passes; whitespace-only content then 400s at the
    # handler's first validation (before any DB touch, keeping this hermetic).
    resp = client.post("/api/notables/paste", json={"raw_text": " " * 10})
    assert resp.status_code == 400
    assert "No notable text" in resp.json()["detail"]


def test_failed_paste_purges_boundary_batch(client, monkeypatch):
    """Regression: a paste that failed after boundary admission
    (e.g. the DB died mid-pipeline) left the staged
    {batch_id}-pasted.txt + manifest in the quarantine
    directory forever — an orphaned, unlinked copy of the
    pasted content. The failure path must purge the batch
    when the pipeline failed before its inserts committed."""
    import db.models as db_models
    from services import splunk_boundary

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(db_models, "SessionLocal", _boom)

    resp = client.post("/api/notables/paste",
                       json={"raw_text": "Notable\n\nTitle: t\nHost: h\n"})
    assert resp.status_code == 500
    # The admitted batch (staged .txt + manifest) is gone —
    # nothing is left behind in the quarantine directory.
    assert splunk_boundary.list_batches() == []
    assert list(splunk_boundary.staging_dir().glob("*")) == []


def test_paste_payload_cap_413_multibyte(client, monkeypatch):
    """Regression: PASTE_MAX_BYTES is a BYTE budget. A multibyte
    payload that is under the cap in characters but over it in
    UTF-8 bytes must still 413 — the gate used to measure
    len(raw_text), letting ~4x the budget past it."""
    import api.routes.notables as notables_route

    monkeypatch.setattr(notables_route, "PASTE_MAX_BYTES", 10)
    # 6 characters (a character-count gate admits this) but
    # 12 UTF-8 bytes (over the 10-byte budget).
    resp = client.post("/api/notables/paste", json={"raw_text": "é" * 6})
    assert resp.status_code == 413
    assert "exceeds" in resp.json()["detail"]


def test_analysis_draft_roundtrip(client, with_api_key, monkeypatch):
    """Autosave drafts (ported from main): PUT persists a snapshot inside the
    investigation state; the GET read strips it back out as draft_state."""
    import db.models as db_models
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_models.Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", test_session)

    db = test_session()
    db.add(db_models.TriageResult(case_id="DRAFT-1", rule_name="r1", analysis_summary="s"))
    db.commit()

    snap = {"analysisContext": "half-written notes", "analysisModel": "llama3.1:latest"}
    resp = client.put(
        "/api/db/triage/DRAFT-1/analysis-draft",
        json={"snapshot": snap},
        headers={"X-API-Key": "test-secret-key"},
    )
    assert resp.status_code == 200 and resp.json()["success"] is True

    read = client.get(
        "/api/db/triage/DRAFT-1/investigation-state",
        headers={"X-API-Key": "test-secret-key"},
    )
    assert read.status_code == 200
    body = read.json()
    assert body["draft_state"] == snap
    assert "_draft_state" not in (body.get("evidence_summary") or {}), "internal key must not leak"

    # A case with no state row still reports a null draft cleanly.
    db.add(db_models.TriageResult(case_id="DRAFT-2", rule_name="r2", analysis_summary="s"))
    db.commit()
    empty = client.get("/api/db/triage/DRAFT-2/investigation-state", headers={"X-API-Key": "test-secret-key"})
    assert empty.status_code == 200 and empty.json()["draft_state"] is None
    engine.dispose()


def test_rate_limit_429(client, monkeypatch):
    """C2.1.5: Ollama-backed POSTs are limited to RATE_LIMIT_PER_MIN per IP.
    The limiter counts requests before handlers run, so handler outcomes
    (500 without a DB here) do not matter."""
    monkeypatch.setattr(api_main, "RATE_LIMIT_PER_MIN", 2)
    api_main._RATE_HITS.clear()
    try:
        for _ in range(2):
            client.post("/api/db/analyze", json={"case_id": "x"})
        resp = client.post("/api/db/analyze", json={"case_id": "x"})
        assert resp.status_code == 429
        assert resp.json()["detail"] == "Too many requests"
        # Non-listed surface unaffected.
        assert client.get("/api/health").status_code == 200
    finally:
        api_main._RATE_HITS.clear()
