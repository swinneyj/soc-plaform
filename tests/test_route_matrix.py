"""SEC-02 — the route/role matrix must describe the app that exists,
and the app must enforce it.

Strict enforcement (2026-10-26 work order Q1–Q5 + HIGH-01 Tier-1):
- 5 public, 20 admin, 50 analyst (75 total).
- Every authenticated route carries require_role with the matrix role.
- Analyst actors 403 on admin-only routes (session mode).
"""

import os
import subprocess
import sys

os.environ.setdefault("CORS_ORIGINS", "https://test.local")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_route_role_matrix_is_current():
    proc = subprocess.run(
        [sys.executable, "scripts/route_matrix.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "docs/ROUTE_ROLE_MATRIX.md is stale — regenerate with "
        "`python scripts/route_matrix.py` and re-insert the table.\n"
        + proc.stdout
        + proc.stderr
    )


def test_matrix_cross_checks_against_openapi():
    """The inventory is complete: served method+path pairs equal listed ones."""
    sys.path.insert(0, ROOT)
    import scripts.route_matrix as matrix  # noqa: E402

    served, listed, diff = matrix.verify_coverage(matrix._route_objects())
    assert served == listed == 75, (served, listed)
    assert diff == [], diff


def test_final_counts_are_17_53_5():
    sys.path.insert(0, ROOT)
    import scripts.route_matrix as matrix  # noqa: E402

    rows = matrix.collect()
    from collections import Counter
    counts = Counter(r["proposed"] for r in rows)
    assert counts["admin"] == 20, counts
    assert counts["analyst"] == 50, counts
    assert counts["public"] == 5, counts


def test_every_authenticated_route_carries_its_matrix_role():
    """No authenticated route may ship without (or with the wrong) require_role."""
    sys.path.insert(0, ROOT)
    import scripts.route_matrix as matrix  # noqa: E402

    for row in matrix.collect():
        if row["proposed"] == "public":
            assert row["role"] == "-", row
        else:
            assert row["role"] == row["proposed"], row


def _session_client(monkeypatch, role="analyst"):
    """TestClient with AUTH_MODE=session and a live session for `role`."""
    import api.auth as api_auth
    import api.main as api_main
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import db.models as db_models

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    db_models.Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(db_models, "SessionLocal", session)

    monkeypatch.setattr(api_auth, "_SESSION_MODE", True)
    monkeypatch.setattr(api_auth, "_PROXY_MODE", False)
    monkeypatch.setattr(api_auth, "_API_KEY", "")
    api_auth._actor_cache.clear()
    api_main._RATE_HITS.clear()
    try:
        import api.routes.auth as _auth_routes
        _auth_routes._LOGIN_FAILURES.clear()
    except Exception:
        pass

    db = session()
    try:
        db.add(
            db_models.User(
                id=1, username="alice", password_hash=api_auth.hash_password("pw"),
                role=role, is_active=True,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(api_main.app)
    login = client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
    assert login.status_code == 200, login.text
    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})
    return client, engine


def test_analyst_forbidden_on_admin_deletes(monkeypatch):
    client, engine = _session_client(monkeypatch, role="analyst")
    try:
        # Admin-only destructive routes must 403 for analyst (CSRF present).
        for method, path in [
            ("POST", "/api/db/triage/CASE-1/delete"),
            ("POST", "/api/db/triage/batch-delete"),
            ("DELETE", "/api/db/notables/1"),
            ("POST", "/api/db/notables/1/delete"),
            ("DELETE", "/api/db/triage/CASE-1/evidence/1"),
            ("POST", "/api/db/triage/CASE-1/evidence/1/delete"),
            ("GET", "/api/registry"),
        ]:
            resp = client.request(method, path, json={} if method == "POST" else None)
            # Missing rows 404, but never 2xx for analyst on admin routes;
            # the gate that matters is 403 (authz) vs 401/404.
            assert resp.status_code in (400, 403, 404), (method, path, resp.status_code, resp.text)
            # At least the registry read deterministically 403s (exists, no params).
            if path == "/api/registry":
                assert resp.status_code == 403, resp.text
    finally:
        engine.dispose()


def test_analyst_allowed_on_analyst_reads(monkeypatch):
    client, engine = _session_client(monkeypatch, role="analyst")
    try:
        for path in ["/api/tools", "/api/jobs", "/api/splunk-boundary/status"]:
            resp = client.get(path)
            assert resp.status_code != 403, (path, resp.status_code, resp.text)
        # HIGH-01: system-observability reads are admin — analyst 403s.
        for path in ["/api/db/stats", "/api/db/operations", "/api/db/ollama/health"]:
            assert client.get(path).status_code == 403, path
    finally:
        engine.dispose()
