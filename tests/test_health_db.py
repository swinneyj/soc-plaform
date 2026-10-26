"""OPS-01 — GET /api/health probes PostgreSQL with a fast SELECT 1."""

import os

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

from fastapi.testclient import TestClient

import api.main as api_main


def test_api_health_reports_connected_when_db_reachable():
    client = TestClient(api_main.app)
    resp = client.get("/api/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["db"] == "connected"


def test_api_health_503_when_db_unreachable(monkeypatch):
    import db.models as db_models

    def _boom(*args, **kwargs):
        raise ConnectionError("db down")

    monkeypatch.setattr(db_models, "create_database_engine", _boom)
    # api/routes/system.py imports create_database_engine inside the handler
    # from db.models, so patching the attribute is enough.
    client = TestClient(api_main.app)
    resp = client.get("/api/health")
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["db"] == "disconnected"


def test_liveness_health_stays_simple():
    client = TestClient(api_main.app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
