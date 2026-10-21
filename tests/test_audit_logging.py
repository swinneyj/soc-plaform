"""Tests for the structured JSON security audit stream (task 3).

Every test captures the real pipeline: audit_event -> soc.audit logger ->
JsonFormatter -> a StringIO handler swapped in for the stdout one, so the
assertions run against the exact bytes an operator would see on stdout.
"""
import io
import json
import logging
import os

# CORS middleware is mounted at api.main import time only when CORS_ORIGINS
# is set; set it here, before the import, as a hard requirement.
os.environ.setdefault("CORS_ORIGINS", "https://test.local")

import pytest
from fastapi.testclient import TestClient

import api.auth as api_auth
import api.main as api_main
from api.helpers import audit


@pytest.fixture(autouse=True)
def isolated_boundary_staging(tmp_path, monkeypatch):
    """C4: route pastes stage boundary batches — keep them out of the
    repo's Data/quarantine during tests (same as test_api_key_gate)."""
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(tmp_path / "quarantine"))


@pytest.fixture()
def audit_lines():
    """Swap the soc.audit stdout handler for a StringIO one; return the
    buffer so tests can parse the emitted JSON lines. Other tests'
    requests between fixture setup and teardown can also land here, so
    tests should filter by event name."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(audit.JsonFormatter())
    logger = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = list(logger.handlers)
    logger.handlers = [handler]
    try:
        yield stream
    finally:
        logger.handlers = saved


def _events(stream):
    """Parsed audit payloads from the captured stream."""
    return [
        json.loads(line)
        for line in stream.getvalue().splitlines()
        if line.strip()
    ]


def _by_event(stream, name):
    return [e for e in _events(stream) if e.get("event") == name]


@pytest.fixture()
def client():
    api_main._RATE_HITS.clear()
    return TestClient(api_main.app)


@pytest.fixture()
def with_api_key(monkeypatch):
    monkeypatch.setattr(api_auth, "_API_KEY", "test-secret-key")
    yield "test-secret-key"


@pytest.fixture()
def session_client(monkeypatch):
    """AUTH_MODE=session TestClient against an isolated SQLite DB
    (same pattern as test_api_key_gate.session_client)."""
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

    # The actor cache is module-global: clear around the test so a
    # previous test's cookie cannot resolve here.
    api_auth._actor_cache.clear()
    client = TestClient(api_main.app)
    client.test_session = test_session
    yield client
    api_auth._actor_cache.clear()
    engine.dispose()


def _make_user(db, username="alice", role="analyst", password="pw123"):
    from db.models import User

    user = User(username=username, role=role,
                password_hash=api_auth.hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Formatter + allowlist unit tests
# ---------------------------------------------------------------------------

def test_json_line_shape(audit_lines):
    audit.audit_event(
        "unit_probe", ip="10.0.0.1", status=200, actor="session",
        actor_role="analyst", username="alice", reason="probe",
    )
    lines = _events(audit_lines)
    assert len(lines) == 1
    entry = lines[0]
    assert entry["event"] == "unit_probe"
    assert entry["message"] == "unit_probe"
    assert entry["logger"] == audit.AUDIT_LOGGER_NAME
    assert entry["level"] == "INFO"
    assert entry["ts"].endswith("Z")
    assert entry["ip"] == "10.0.0.1"
    # Only envelope + allowlisted fields — nothing extra can appear.
    assert set(entry) <= {"ts", "level", "logger", "message"} | audit.AUDIT_FIELDS


def test_denied_fields_raise(audit_lines):
    """Credential/payload names must fail loudly, never be serialized."""
    with pytest.raises(ValueError):
        audit.audit_event("x", password="hunter2")
    with pytest.raises(ValueError):
        audit.audit_event("x", api_key="k")
    with pytest.raises(ValueError):
        audit.audit_event("x", body="raw payload")
    with pytest.raises(ValueError):
        audit.audit_event("x", csrf_token="t")
    assert _events(audit_lines) == []


def test_unknown_fields_raise(audit_lines):
    """Fields outside AUDIT_FIELDS are rejected too — extending the
    schema is a deliberate two-way-allowlist edit, never an accident."""
    with pytest.raises(ValueError):
        audit.audit_event("x", user_agent="Mozilla/5.0")
    assert _events(audit_lines) == []


def test_attacker_controlled_username_truncated(audit_lines):
    audit.audit_event("x", username="A" * 5000)
    (entry,) = _events(audit_lines)
    assert len(entry["username"]) < 600
    assert entry["username"].endswith("(truncated)")


def test_audit_logger_does_not_propagate():
    """The audit stream must stay isolated from root/uvicorn handlers."""
    assert logging.getLogger(audit.AUDIT_LOGGER_NAME).propagate is False


# ---------------------------------------------------------------------------
# File output (task 4 premise: the K8s sidecar tails a JSON FILE)
# ---------------------------------------------------------------------------

def test_audit_file_output_writes_same_json_lines(tmp_path):
    """_attach_file_handler writes identical JSON lines to the file the
    sidecar will tail; parent dirs are created (fresh pod emptyDir)."""
    from pathlib import Path

    path = str(tmp_path / "soc" / "audit.json")  # nested on purpose
    lg = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = list(lg.handlers)
    lg.handlers = []  # isolate: file test must not also hit stdout
    handler = audit._attach_file_handler(lg, path)
    assert handler is not None, "file handler failed to open"
    try:
        audit.audit_event("file_probe", username="alice", status=200)
        handler.flush()
        lines = [l for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
        (entry,) = [json.loads(l) for l in lines]
        assert entry["event"] == "file_probe"
        assert entry["username"] == "alice"
        assert entry["ts"].endswith("Z")  # same formatter as stdout
    finally:
        lg.removeHandler(handler)
        handler.close()
        lg.handlers = saved


def test_audit_file_failure_is_nonfatal(tmp_path):
    """An unwritable AUDIT_LOG_FILE degrades to None + stderr warning —
    audit must never block startup."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")  # exists as a FILE -> makedirs below must fail
    lg = logging.getLogger(audit.AUDIT_LOGGER_NAME)
    saved = list(lg.handlers)
    lg.handlers = []
    try:
        handler = audit._attach_file_handler(
            lg, str(blocker / "audit.json"))
        assert handler is None
        assert not lg.handlers  # nothing half-attached
    finally:
        lg.handlers = saved


# ---------------------------------------------------------------------------
# Login events (api.routes.auth)
# ---------------------------------------------------------------------------

def test_login_failure_audited(session_client, audit_lines):
    db = session_client.test_session()
    _make_user(db, username="bob", password="pw123")
    resp = session_client.post(
        "/api/auth/login", json={"username": "bob", "password": "CANARY_PW_9f3a"})
    assert resp.status_code == 401
    (event,) = _by_event(audit_lines, "login_failure")
    assert event["level"] == "WARNING"
    assert event["username"] == "bob"
    assert event["status"] == 401
    assert event["reason"] == "invalid_credentials"
    assert event["ip"]
    # The attempted password never reaches the audit stream.
    assert "CANARY_PW_9f3a" not in audit_lines.getvalue()


def test_login_failure_unknown_user_same_reason(session_client, audit_lines):
    """No username-enumeration signal in the audit stream either: a
    missing user logs the same reason as a wrong password."""
    resp = session_client.post(
        "/api/auth/login", json={"username": "ghost", "password": "x"})
    assert resp.status_code == 401
    (event,) = _by_event(audit_lines, "login_failure")
    assert event["reason"] == "invalid_credentials"


def test_login_success_audited(session_client, audit_lines):
    db = session_client.test_session()
    _make_user(db, username="alice", role="analyst", password="pw123")
    resp = session_client.post(
        "/api/auth/login", json={"username": "alice", "password": "pw123"})
    assert resp.status_code == 200
    (event,) = _by_event(audit_lines, "login_success")
    assert event["username"] == "alice"
    assert event["actor_role"] == "analyst"
    assert event["status"] == 200
    assert event["request_id"] and event["request_id"] != "?"


def test_login_throttled_audited(session_client, audit_lines, monkeypatch):
    monkeypatch.setattr("api.routes.auth.LOGIN_MAX_FAILURES", 1)
    db = session_client.test_session()
    _make_user(db, username="carol", password="pw123")
    session_client.post("/api/auth/login", json={"username": "carol", "password": "bad"})
    resp = session_client.post("/api/auth/login", json={"username": "carol", "password": "bad"})
    assert resp.status_code == 429
    assert _by_event(audit_lines, "login_failure")
    (event,) = _by_event(audit_lines, "login_throttled")
    assert event["level"] == "WARNING"
    assert event["status"] == 429


def test_logout_audited(session_client, audit_lines):
    db = session_client.test_session()
    _make_user(db, username="dave", password="pw123")
    login = session_client.post(
        "/api/auth/login", json={"username": "dave", "password": "pw123"})
    csrf = login.json()["csrf_token"]
    out = session_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert out.status_code == 200
    (event,) = _by_event(audit_lines, "logout")
    assert event["actor"] == "session"
    assert event["username"] == "dave"


# ---------------------------------------------------------------------------
# Authorization failures (gate middleware + route dependencies)
# ---------------------------------------------------------------------------

def test_gate_rejection_audited_api_key_mode(client, with_api_key, audit_lines):
    resp = client.post("/api/execute", json={"tool_name": "anything"})
    assert resp.status_code == 401
    (event,) = _by_event(audit_lines, "auth_rejected")
    assert event["level"] == "WARNING"
    assert event["status"] == 401
    assert event["reason"] == "missing_api_key"
    assert event["path"] == "/api/execute"
    assert event["method"] == "POST"
    # The rejection response is correlated with the audit line.
    assert resp.headers.get("X-Request-Id") == event["request_id"]


def test_csrf_rejection_audited(session_client, audit_lines):
    db = session_client.test_session()
    _make_user(db, username="erin", password="pw123")
    session_client.post(
        "/api/auth/login", json={"username": "erin", "password": "pw123"})
    # Cookie-backed mutation without X-CSRF-Token: rejected by the gate.
    resp = session_client.post("/api/notables/paste", json={"raw_text": "x"})
    assert resp.status_code == 403
    (event,) = _by_event(audit_lines, "auth_rejected")
    assert event["reason"] == "csrf"
    assert event["status"] == 403
    assert event["actor"] == "session"
    assert event["username"] == "erin"


def test_route_role_denial_audited(session_client, audit_lines):
    """A require_role 403 raised inside a route (not the gate) is audited
    by the HTTPException handler."""
    db = session_client.test_session()
    _make_user(db, username="frank", role="analyst", password="pw123")
    login = session_client.post(
        "/api/auth/login", json={"username": "frank", "password": "pw123"})
    csrf = login.json()["csrf_token"]
    resp = session_client.delete(
        "/api/jobs/some-job-id", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 403
    (event,) = _by_event(audit_lines, "auth_rejected")
    assert event["reason"] == "route_authorization"
    assert event["status"] == 403
    assert event["actor_role"] == "analyst"
    assert event["username"] == "frank"


def test_session_poll_not_audited(session_client, audit_lines):
    """A logged-out UI session poll (/api/auth/session -> 401) must not
    spam the audit stream — EXEMPT_PATHS carry their own events only."""
    resp = session_client.get("/api/auth/session")
    assert resp.status_code == 401
    assert _events(audit_lines) == []


# ---------------------------------------------------------------------------
# State changes + no-leak guarantees
# ---------------------------------------------------------------------------

def test_state_change_audited_api_key_mode(client, with_api_key, audit_lines):
    """A successful mutation is audited with the machine-actor attribution."""
    resp = client.delete("/api/jobs", headers={"X-API-Key": "test-secret-key"})
    assert resp.status_code == 200
    (event,) = _by_event(audit_lines, "state_change")
    assert event["actor"] == "api-key"
    assert event["actor_role"] == "admin"
    assert event["path"] == "/api/jobs"
    assert event["method"] == "DELETE"
    assert event["status"] == 200
    assert event["request_id"] and event["request_id"] != "?"


def test_state_change_audited_session_mode(session_client, audit_lines):
    """An analyst performing a non-admin mutation (notables paste — the
    route family the security review confirms needs only
    authenticated+CSRF) is attributed in the state_change event."""
    db = session_client.test_session()
    _make_user(db, username="grace", role="analyst", password="pw123")
    login = session_client.post(
        "/api/auth/login", json={"username": "grace", "password": "pw123"})
    csrf = login.json()["csrf_token"]
    resp = session_client.post(
        "/api/notables/paste", json={"raw_text": "audit probe"},
        headers={"X-CSRF-Token": csrf})
    assert resp.status_code < 400, resp.text
    (event,) = _by_event(audit_lines, "state_change")
    assert event["actor"] == "session"
    assert event["actor_role"] == "analyst"
    assert event["username"] == "grace"
    assert event["path"].startswith("/api/")
    # The pasted text itself never reaches the audit stream.
    assert "audit probe" not in audit_lines.getvalue()


def test_failed_mutation_not_audited_as_state_change(client, with_api_key, audit_lines):
    """The rejected request produces auth_rejected, never state_change."""
    resp = client.post("/api/execute", json={"tool_name": "x"})
    assert resp.status_code == 401
    assert _by_event(audit_lines, "state_change") == []
    assert _by_event(audit_lines, "auth_rejected")


def test_no_bodies_credentials_or_cookies_in_stream(client, with_api_key, audit_lines):
    """Canary sweep: rejected AND successful requests carry secret-ish
    material (body canary, wrong-key canary) that must never appear in
    any audit line."""
    canary_body = "CANARY_BODY_d4ad"
    canary_key = "CANARY_KEY_c0de"
    # Rejected mutation with a body + wrong key.
    client.post("/api/execute", json={"tool_name": canary_body},
                headers={"X-API-Key": canary_key})
    # Successful mutation with the real key.
    client.delete("/api/jobs", headers={"X-API-Key": "test-secret-key"})
    # Read with the key in the query string (path must never carry it).
    client.get("/api/health?api_key=test-secret-key")
    output = audit_lines.getvalue()
    assert _by_event(audit_lines, "auth_rejected")
    assert _by_event(audit_lines, "state_change")
    assert canary_body not in output
    assert canary_key not in output
    assert "test-secret-key" not in output
    for entry in _events(audit_lines):
        # Query strings never appear in the path field.
        assert "?" not in entry.get("path", "")
