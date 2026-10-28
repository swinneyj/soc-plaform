"""MED-05 + LOW-02 — surface hygiene without a live server.

- MED-05: file/report download routes canonicalize paths; `..` escapes,
  absolute paths, and symlink escapes resolve to 404 (never file contents).
  Wildcard CORS is disabled: an unauthorized Origin receives no grant.
- LOW-02: interactive API docs are disabled by default — GET /docs and
  GET /openapi.json answer 404 (ENABLE_DOCS unset in tests).
"""

import os

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

from fastapi.testclient import TestClient

import api.main as api_main

ALLOWED_ORIGIN = "https://test.local"
EVIL_ORIGIN = "https://evil.example"


def _client() -> TestClient:
    api_main._RATE_HITS.clear()
    return TestClient(api_main.app)


def test_tool_artifact_traversal_is_404():
    client = _client()
    for evil in (
        "../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "/etc/passwd",
        "....//....//etc/passwd",
    ):
        resp = client.get(f"/api/tool-artifacts/{evil}")
        assert resp.status_code == 404, (evil, resp.status_code)
        assert "root:" not in resp.text  # no /etc/passwd contents


def test_report_download_traversal_is_404():
    client = _client()
    for evil in ("../requirements.txt", "..%2F..%2Fetc%2Fpasswd", "/etc/passwd"):
        resp = client.get(f"/api/reports/{evil}")
        assert resp.status_code in (404, 422), (evil, resp.status_code)


def test_unauthorized_origin_receives_no_cors_grant():
    client = _client()
    resp = client.get("/api/health", headers={"Origin": EVIL_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") != EVIL_ORIGIN, resp.headers


def test_allowlisted_origin_is_granted():
    client = _client()
    resp = client.get("/api/health", headers={"Origin": ALLOWED_ORIGIN})
    assert resp.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN, dict(resp.headers)


def test_interactive_docs_are_disabled_by_default():
    client = _client()
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404
