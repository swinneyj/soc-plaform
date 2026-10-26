"""SEC-04 — endpoint rate-limiter coverage (429 behavior)."""

import os

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

from fastapi.testclient import TestClient

import api.main as api_main
import api.middleware as mw


def test_mutating_api_rate_limited_to_429(monkeypatch):
    monkeypatch.setattr(mw, "RATE_LIMIT_PER_MIN", 3)
    api_main._RATE_HITS.clear()
    try:
        client = TestClient(api_main.app)
        statuses = []
        for _ in range(4):
            r = client.post("/api/db/triage/batch-delete", json={"case_ids": []})
            statuses.append(r.status_code)
        # Pass-through = any non-429 (400 validation or 401 gate depending on env).
        assert all(s != 429 for s in statuses[:3]), statuses
        assert statuses[3] == 429, statuses
        assert client.post("/api/db/triage/batch-delete", json={"case_ids": []}).status_code == 429
    finally:
        api_main._RATE_HITS.clear()


def test_reads_are_not_rate_limited(monkeypatch):
    monkeypatch.setattr(mw, "RATE_LIMIT_PER_MIN", 2)
    api_main._RATE_HITS.clear()
    try:
        client = TestClient(api_main.app)
        for _ in range(5):
            r = client.get("/api/health")
            assert r.status_code == 200, r.text
    finally:
        api_main._RATE_HITS.clear()


def test_login_has_its_own_throttle_not_the_generic_limiter(monkeypatch):
    """Login 429s come from the auth throttle even when the generic window is empty."""
    import api.routes.auth as auth_routes

    monkeypatch.setattr(mw, "RATE_LIMIT_PER_MIN", 1000)
    api_main._RATE_HITS.clear()
    auth_routes._LOGIN_FAILURES.clear()
    try:
        client = TestClient(api_main.app)
        # 5 failures allowed, 6th throttled by the login windows.
        for i in range(6):
            r = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
            if i < 5:
                assert r.status_code == 401, (i, r.status_code)
            else:
                assert r.status_code == 429, (i, r.status_code, r.text)
    finally:
        api_main._RATE_HITS.clear()
        auth_routes._LOGIN_FAILURES.clear()
