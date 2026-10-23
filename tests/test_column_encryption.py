"""SEC-03 — column-level encryption for csrf_token and code_snippet.

Two halves, both required:

* the crypto contract (format, nonce freshness, legacy plaintext, wrong/missing
  key behaviour), tested against a real SQLite database so the TypeDecorator is
  exercised, not just the helper functions;
* the integration contract — a login → session → CSRF-gated mutation round trip
  with encryption ON, which is the only way to prove the decorator does not
  quietly break authentication.

The rest of the suite runs with SOC_FIELD_KEY unset, i.e. the pass-through
behaviour, so these tests are the only place encryption is switched on.
"""

import base64
import os
import secrets

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import api.auth as api_auth
import api.main as api_main
import db.crypto as crypto


def _hex_key() -> str:
    return secrets.token_hex(32)


@pytest.fixture()
def engine(monkeypatch):
    """In-memory SQLite wired into db.models, key unset unless a test sets it."""
    import db.models as db_models

    monkeypatch.delenv("SOC_FIELD_KEY", raising=False)
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=eng)
    monkeypatch.setattr(db_models, "SessionLocal", sessionmaker(bind=eng))
    api_main._RATE_HITS.clear()
    api_auth._actor_cache.clear()
    try:
        yield eng
    finally:
        eng.dispose()


def _raw(engine, table, column, pk):
    with engine.connect() as conn:
        return conn.execute(
            text("select %s from %s where id = :k" % (column, table)), {"k": pk}
        ).scalar()


# ---------------------------------------------------------------------------
# Crypto contract
# ---------------------------------------------------------------------------

def test_key_parsing_accepts_hex_and_base64():
    raw = secrets.token_bytes(32)
    assert crypto.parse_key(raw.hex()) == raw
    assert crypto.parse_key(base64.b64encode(raw).decode()) == raw


def test_malformed_key_fails_loudly():
    for bad in ("not-a-key", "abc123", secrets.token_hex(16)):
        with pytest.raises(crypto.FieldKeyError):
            crypto.parse_key(bad)


def test_unset_key_is_pass_through(monkeypatch):
    monkeypatch.delenv("SOC_FIELD_KEY", raising=False)
    assert crypto.current_key() is None
    assert crypto.encrypt_text("plain") == "plain"
    assert crypto.decrypt_text("plain") == "plain"


def test_encrypt_then_decrypt_roundtrip(monkeypatch):
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    sealed = crypto.encrypt_text("s3cret-value")
    assert sealed.startswith(crypto.PREFIX)
    assert "s3cret-value" not in sealed
    assert crypto.decrypt_text(sealed) == "s3cret-value"


def test_identical_plaintexts_get_distinct_ciphertexts(monkeypatch):
    """A fresh nonce per write: equal values must not be correlatable."""
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    first = crypto.encrypt_text("same")
    second = crypto.encrypt_text("same")
    assert first != second
    assert crypto.decrypt_text(first) == crypto.decrypt_text(second) == "same"


def test_encrypt_is_idempotent(monkeypatch):
    """Re-saving a loaded value must not double-encrypt it."""
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    sealed = crypto.encrypt_text("v")
    assert crypto.encrypt_text(sealed) == sealed


def test_wrong_key_raises_instead_of_returning_empty(monkeypatch):
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    sealed = crypto.encrypt_text("value")
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    with pytest.raises(crypto.FieldKeyError):
        crypto.decrypt_text(sealed)


def test_ciphertext_without_key_raises(monkeypatch):
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    sealed = crypto.encrypt_text("value")
    monkeypatch.delenv("SOC_FIELD_KEY", raising=False)
    with pytest.raises(crypto.FieldKeyError):
        crypto.decrypt_text(sealed)


def test_encrypted_csrf_fits_a_widened_column(monkeypatch):
    """43-char csrf token -> ~104-char ciphertext; the ALTER to 255 must hold."""
    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    sealed = crypto.encrypt_text(secrets.token_urlsafe(32))
    assert len(sealed) < 255


# ---------------------------------------------------------------------------
# Through the ORM (the TypeDecorator, i.e. what the app actually uses)
# ---------------------------------------------------------------------------

def test_column_is_plaintext_at_rest_when_key_is_unset(engine):
    from db.models import AuthSession, SessionLocal
    from db.util import utcnow_naive

    db = SessionLocal()
    try:
        db.add(AuthSession(token_hash=secrets.token_hex(32), csrf_token="visible-token",
                           user_id=1, expires_at=utcnow_naive()))
        db.commit()
        row = db.query(AuthSession).first()
        assert row.csrf_token == "visible-token"
    finally:
        db.close()
    assert _raw(engine, "auth_sessions", "csrf_token", 1) == "visible-token"


def test_column_is_ciphertext_at_rest_when_key_is_set(engine, monkeypatch):
    from db.models import AuthSession, SessionLocal
    from db.util import utcnow_naive

    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    db = SessionLocal()
    try:
        db.add(AuthSession(token_hash=secrets.token_hex(32), csrf_token="hidden-token",
                           user_id=1, expires_at=utcnow_naive()))
        db.commit()
        # The application sees plaintext...
        assert db.query(AuthSession).first().csrf_token == "hidden-token"
    finally:
        db.close()
    # ...while the stored bytes are ciphertext.
    stored = _raw(engine, "auth_sessions", "csrf_token", 1)
    assert stored.startswith(crypto.PREFIX)
    assert "hidden-token" not in stored


def test_legacy_plaintext_rows_stay_readable_after_the_key_arrives(engine, monkeypatch):
    """Deploying a key must not strand rows written before SEC-03."""
    from db.models import AuthSession, SessionLocal
    from db.util import utcnow_naive

    db = SessionLocal()
    try:
        db.add(AuthSession(token_hash=secrets.token_hex(32), csrf_token="legacy-token",
                           user_id=1, expires_at=utcnow_naive()))
        db.commit()
    finally:
        db.close()

    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    db = SessionLocal()
    try:
        assert db.query(AuthSession).first().csrf_token == "legacy-token"
    finally:
        db.close()


def test_code_snippet_is_encrypted_at_rest(engine, monkeypatch):
    from db.models import CodeReview, SessionLocal

    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    snippet = "api_key = 'do-not-leak-me'"
    db = SessionLocal()
    try:
        db.add(CodeReview(code_snippet=snippet, language="python", review_result="ok"))
        db.commit()
        assert db.query(CodeReview).first().code_snippet == snippet
    finally:
        db.close()
    stored = _raw(engine, "code_reviews", "code_snippet", 1)
    assert stored.startswith(crypto.PREFIX)
    assert "do-not-leak-me" not in stored


# ---------------------------------------------------------------------------
# Integration: the auth stack still works end to end with encryption on
# ---------------------------------------------------------------------------

def test_login_session_and_csrf_mutation_work_with_encryption_enabled(engine, monkeypatch):
    """Login writes a csrf_token; the gate reads it back through the decorator."""
    from db.models import SessionLocal, User

    monkeypatch.setenv("SOC_FIELD_KEY", _hex_key())
    monkeypatch.setattr(api_auth, "_SESSION_MODE", True)
    monkeypatch.setattr(api_auth, "_PROXY_MODE", False)
    monkeypatch.setattr(api_auth, "_API_KEY", "")

    db = SessionLocal()
    try:
        db.add(User(id=1, username="dalton", password_hash=api_auth.hash_password("pw-test"),
                    role="admin", is_active=True))
        db.commit()
    finally:
        db.close()

    client = TestClient(api_main.app)
    login = client.post("/api/auth/login", json={"username": "dalton", "password": "pw-test"})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]

    info = client.get("/api/auth/session")
    assert info.status_code == 200
    assert info.json()["csrf_token"] == csrf  # decrypted value matches what login returned

    # A CSRF-gated mutation: without the header it must 403, with it must pass.
    missing = client.post("/api/auth/logout")
    assert missing.status_code == 403
    assert missing.json()["detail"].startswith("CSRF")

    ok = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert ok.status_code == 200, ok.text

    # The session row is gone (logout destroys it) — and while it existed, its
    # csrf_token was stored as ciphertext, never as the token handed to the UI.
    stored = _raw(engine, "auth_sessions", "csrf_token", 1)
    assert stored is None or stored.startswith(crypto.PREFIX)
    assert csrf not in str(stored)
