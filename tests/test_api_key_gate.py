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


def test_tool_subprocess_env_is_minimal(tmp_path, monkeypatch):
    """C2.1.6: a registered tool subprocess runs on an explicit
    env allowlist (PATH/HOME/COMMANDER_BOOT), never a copy of
    the API environment — DATABASE_URL, API_KEY and
    OLLAMA_API_KEY must be unreadable by the tool even when
    they are set in the API process."""
    import json

    import api.routes.tools as tools_route

    monkeypatch.setenv("DATABASE_URL", "postgres://leak:leak@db/neon")
    monkeypatch.setenv("API_KEY", "leaked-api-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "leaked-ollama-key")

    probe = tmp_path / "env_probe.py"
    probe.write_text(
        "import json, os\nprint(json.dumps(sorted(os.environ)))\n",
        encoding="utf-8",
    )
    result = tools_route.execute_tool_sync(str(probe), {})
    assert result["exit_code"] == 0
    tool_env_keys = json.loads(result["stdout"])
    for required in ("PATH", "HOME", "COMMANDER_BOOT"):
        assert required in tool_env_keys
    for secret in ("DATABASE_URL", "API_KEY", "OLLAMA_API_KEY"):
        assert secret not in tool_env_keys


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


# ---------------------------------------------------------------------------
# F5 — login throttling + constant-time user resolution
# (API security review: /api/auth/login short-circuited on a missing
# user — a valid username cost a full KDF run, an invalid one returned
# in microseconds, a reliable username-enumeration timing side channel —
# and the endpoint had no brute-force protection)
# ---------------------------------------------------------------------------

import time as _time

import api.routes.auth as auth_route


@pytest.fixture()
def clean_login_throttle_state():
    """Login failure windows are module-global; keep every
    F5 test hermetic and leave no state behind for others."""
    auth_route._LOGIN_FAILURES.clear()
    yield
    auth_route._LOGIN_FAILURES.clear()


def _spy_verify_password(monkeypatch):
    """Wrap api.auth.verify_password, recording the hash each
    attempt was verified against, and return the recording list."""
    verify_calls = []
    real_verify = api_auth.verify_password

    def _spy(password, encoded):
        verify_calls.append(encoded)
        return real_verify(password, encoded)

    monkeypatch.setattr(api_auth, "verify_password", _spy)
    return verify_calls


def test_missing_user_login_runs_full_kdf(session_client, monkeypatch):
    """Regression: the login fast-path used to short-circuit on a
    missing user, so no password verification ran at all. Every
    attempt — valid username or not — must now pay one full KDF
    run, verifying against the dummy hash when the user is absent."""
    verify_calls = _spy_verify_password(monkeypatch)

    resp = session_client.post(
        "/api/auth/login",
        json={"username": "ghost-user", "password": "anything"},
    )
    assert resp.status_code == 401
    assert len(verify_calls) == 1
    # Verified against the dummy hash — same KDF, same parameters
    # as a real hash, so a missing user costs a real user's time.
    assert verify_calls[0] == auth_route._dummy_password_hash()
    assert verify_calls[0].startswith(("scrypt$", "pbkdf2$"))


def test_inactive_user_login_verifies_dummy_hash(session_client, monkeypatch):
    """An inactive account also resolves in constant time: verifying
    its real hash would leak that the username exists but is disabled."""
    db = session_client.test_session()
    user = _make_user(db, username="sleeping", password="pw123")
    user.is_active = False
    db.commit()

    verify_calls = _spy_verify_password(monkeypatch)
    resp = session_client.post(
        "/api/auth/login",
        json={"username": "sleeping", "password": "pw123"},
    )
    assert resp.status_code == 401
    assert verify_calls[0] == auth_route._dummy_password_hash()
    assert verify_calls[0] != user.password_hash


def test_active_user_login_verifies_real_hash(session_client, monkeypatch):
    """Constant-time resolution applies to missing/inactive users;
    a real active user still verifies against their stored hash."""
    db = session_client.test_session()
    user = _make_user(db, username="awake", password="pw123")

    verify_calls = _spy_verify_password(monkeypatch)
    resp = session_client.post(
        "/api/auth/login",
        json={"username": "awake", "password": "pw123"},
    )
    assert resp.status_code == 200
    assert verify_calls == [user.password_hash]


def test_missing_and_present_user_login_cost_the_same(session_client):
    """Timing sanity for the enumeration fix: a missing-user login
    and a real-user (wrong-password) login each pay exactly one KDF
    run, so neither is reliably faster. The 40% floor is generous —
    the real-user path additionally does the DB lookup, which only
    makes it slower, so any real asymmetry fails this test."""
    db = session_client.test_session()
    _make_user(db, username="paced", password="pw123")

    def _timed(username, password):
        t0 = _time.perf_counter()
        session_client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        return _time.perf_counter() - t0

    # Warm both paths once (lazy dummy-hash build, connection pools).
    _timed("paced", "wrong")
    _timed("ghost-timing", "wrong")

    present = _timed("paced", "wrong")
    missing = _timed("ghost-timing", "wrong")
    assert missing >= 0.4 * present, (
        f"missing-user login ({missing:.4f}s) is suspiciously faster "
        f"than real-user login ({present:.4f}s) — enumeration channel"
    )


def test_login_throttled_after_repeat_failures(
    session_client, monkeypatch, clean_login_throttle_state
):
    """Failed attempts are counted; once a window is full the login
    answers 429 — checked BEFORE user resolution, so even the correct
    password is refused while throttled."""
    monkeypatch.setattr(auth_route, "LOGIN_MAX_FAILURES", 3)
    db = session_client.test_session()
    _make_user(db, username="throttled-user", password="pw123")

    for _ in range(3):
        resp = session_client.post(
            "/api/auth/login",
            json={"username": "throttled-user", "password": "wrong"},
        )
        assert resp.status_code == 401

    resp = session_client.post(
        "/api/auth/login",
        json={"username": "throttled-user", "password": "pw123"},
    )
    assert resp.status_code == 429
    assert resp.json()["detail"] == "Too many login attempts"


def test_unknown_username_failures_throttle_that_username(
    session_client, monkeypatch, clean_login_throttle_state
):
    """Failed logins for a username that does not exist still count
    against that username's window — the enumeration target itself
    gets throttled."""
    monkeypatch.setattr(auth_route, "LOGIN_MAX_FAILURES", 2)
    for _ in range(2):
        resp = session_client.post(
            "/api/auth/login",
            json={"username": "enumerated", "password": "wrong"},
        )
        assert resp.status_code == 401

    resp = session_client.post(
        "/api/auth/login",
        json={"username": "enumerated", "password": "pw123"},
    )
    assert resp.status_code == 429


def test_login_throttle_windows_are_independent(
    monkeypatch, clean_login_throttle_state
):
    """Per-IP and per-username windows are separate: a full IP
    window throttles every username from that IP, a full username
    window throttles that username from every IP, and neither
    affects unrelated (ip, username) pairs."""
    monkeypatch.setattr(auth_route, "LOGIN_MAX_FAILURES", 2)
    for _ in range(2):
        auth_route._record_login_failure("10.0.0.1", "bob")

    assert auth_route._login_throttled("10.0.0.1", "bob")       # both full
    assert auth_route._login_throttled("10.0.0.1", "alice")     # IP full
    assert auth_route._login_throttled("10.0.0.2", "bob")       # user full
    assert not auth_route._login_throttled("10.0.0.2", "alice")  # neither


def test_login_success_clears_failure_windows(
    session_client, monkeypatch, clean_login_throttle_state
):
    """A successful login resets both windows. With the cap at 3:
    two failures leave the login admitted (2 < 3), the successful
    login clears, and afterwards a single new failure leaves the
    window at 1 — had the clear not happened, the count would be
    3 and the next (correct) login would 429."""
    monkeypatch.setattr(auth_route, "LOGIN_MAX_FAILURES", 3)
    db = session_client.test_session()
    _make_user(db, username="recovered", password="pw123")

    for _ in range(2):
        resp = session_client.post(
            "/api/auth/login",
            json={"username": "recovered", "password": "wrong"},
        )
        assert resp.status_code == 401

    ok = session_client.post(
        "/api/auth/login",
        json={"username": "recovered", "password": "pw123"},
    )
    assert ok.status_code == 200
    # One new failure does not re-arm the throttle (window was cleared).
    session_client.post(
        "/api/auth/login",
        json={"username": "recovered", "password": "wrong"},
    )
    resp = session_client.post(
        "/api/auth/login",
        json={"username": "recovered", "password": "pw123"},
    )
    assert resp.status_code == 200


def test_login_failure_window_expires(
    session_client, monkeypatch, clean_login_throttle_state
):
    """Failures age out of the sliding window: once the window
    collapses, stale failures are pruned and login re-admits."""
    monkeypatch.setattr(auth_route, "LOGIN_MAX_FAILURES", 2)
    db = session_client.test_session()
    _make_user(db, username="expired-window", password="pw123")

    for _ in range(2):
        session_client.post(
            "/api/auth/login",
            json={"username": "expired-window", "password": "wrong"},
        )
    assert session_client.post(
        "/api/auth/login",
        json={"username": "expired-window", "password": "pw123"},
    ).status_code == 429

    # Collapse the window to zero: every recorded failure is now
    # stale and must be pruned on the next probe.
    monkeypatch.setattr(auth_route, "LOGIN_WINDOW_SECONDS", 0)
    resp = session_client.post(
        "/api/auth/login",
        json={"username": "expired-window", "password": "pw123"},
    )
    assert resp.status_code == 200
