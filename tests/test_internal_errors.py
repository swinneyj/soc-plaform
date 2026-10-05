"""F1 regression tests (docs/API_SECURITY_REVIEW.md finding F1).

Raw exception text must never reach a 500 response body —
SQLAlchemy/psycopg messages carry SQL fragments, table names
and host/port; Ollama errors can carry OLLAMA_URL. Every 500
answers with the generic ``{"detail": "Internal error"}`` while
the real cause is logged server-side under the request id that
the request-id middleware stamps onto the request and echoes
back in the ``X-Request-Id`` response header, so a client can
reference a failure without learning anything from it.

Sub-500 details are user-input validation and must pass through
byte-identical; other 5xx codes (e.g. the analysis 504 timeout)
carry static, user-relevant messages and pass through too.
"""

import asyncio
import json
import logging

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

import api.main as api_main
from api.helpers.errors import (
    INTERNAL_ERROR_DETAIL,
    InternalError,
    log_internal_error,
)

# A stand-in for the kind of internal text that must not leak:
# SQL fragments, table names, host/port, credential-bearing URLs.
SECRET_FRAGMENT = "secret-sql-fragment host=db.internal:5432 user=soc"


@pytest.fixture()
def error_logs(caplog):
    caplog.set_level(logging.ERROR, logger="soc.api")
    return caplog


@pytest.fixture()
def client():
    return TestClient(api_main.app)


def _fake_request(rid="rid-abc123", path="/api/db/rules"):
    """A Request with the state the request-id middleware stamps."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
    }
    request = Request(scope)
    request.state.request_id = rid
    return request


# ---------------------------------------------------------------------------
# Exception handlers — direct invocation (deterministic, no routing)
# ---------------------------------------------------------------------------

def test_global_handler_scrubs_detail_and_logs_cause(error_logs):
    """The catch-all handler returns the generic detail, logs the
    real exception (with traceback) under the request id, and
    echoes the request id back in the X-Request-Id header."""
    request = _fake_request()
    response = asyncio.run(
        api_main.global_exception_handler(request, RuntimeError(SECRET_FRAGMENT))
    )
    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in response.body.decode()
    assert response.headers["x-request-id"] == "rid-abc123"

    records = [r for r in error_logs.records if r.name == "soc.api"]
    assert any(
        SECRET_FRAGMENT in r.getMessage() and "rid-abc123" in r.getMessage()
        for r in records
    ), "real cause must be logged server-side under the request id"
    assert any(r.exc_info for r in records), "traceback must be logged"


def test_plain_500_http_exception_is_scrubbed(error_logs):
    """Safety net: a route that still does
    ``raise HTTPException(500, detail=str(e))`` cannot leak — the
    HTTPException handler scrubs any 500 detail and logs the
    cause (the detail itself, when no cause is attached)."""
    request = _fake_request()
    response = asyncio.run(
        api_main._http_exception_handler(
            request, HTTPException(status_code=500, detail=SECRET_FRAGMENT)
        )
    )
    assert response.status_code == 500
    assert json.loads(response.body) == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in response.body.decode()
    assert any(
        SECRET_FRAGMENT in r.getMessage() and "rid-abc123" in r.getMessage()
        for r in error_logs.records
    )


def test_sub_500_detail_passes_through_untouched():
    """4xx details are user-input validation, not internals — they
    must survive byte-identical (the sweep must not over-scrub)."""
    request = _fake_request()
    response = asyncio.run(
        api_main._http_exception_handler(
            request, HTTPException(status_code=404, detail="Not here")
        )
    )
    assert response.status_code == 404
    assert json.loads(response.body) == {"detail": "Not here"}


def test_other_5xx_detail_passes_through_untouched():
    """Non-500 5xx codes carry static, user-relevant messages
    (e.g. the analysis 504 timeout) — only 500 is scrubbed."""
    request = _fake_request()
    response = asyncio.run(
        api_main._http_exception_handler(
            request,
            HTTPException(status_code=504, detail="Analysis timed out"),
        )
    )
    assert response.status_code == 504
    assert json.loads(response.body) == {"detail": "Analysis timed out"}


def test_log_internal_error_accepts_string_cause(error_logs):
    """Tool-output causes (captured stdout/stderr strings, not
    exceptions) log without a traceback and still carry the id."""
    request = _fake_request(rid="rid-str-1")
    log_internal_error(request, "tool exploded: " + SECRET_FRAGMENT)
    records = [r for r in error_logs.records if r.name == "soc.api"]
    assert any(
        SECRET_FRAGMENT in r.getMessage() and "rid-str-1" in r.getMessage()
        for r in records
    )
    assert not any(r.exc_info for r in records)


# ---------------------------------------------------------------------------
# End-to-end through the ASGI stack
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_route():
    """Register throwaway routes on the app for one test and remove
    them afterwards, so the API-endpoint drift guard (which
    enumerates app.router.routes) stays green."""
    registered = []

    def _register(path, endpoint):
        # Inserted at the front: the app mounts StaticFiles at "/"
        # (the web UI), which matches every path, so a route appended
        # after the mount would never be reached.
        route = APIRoute(path, endpoint, methods=["GET"])
        api_main.app.router.routes.insert(0, route)
        registered.append(route)
        return route

    yield _register
    for route in registered:
        api_main.app.router.routes.remove(route)


def test_unhandled_exception_end_to_end(temp_route, error_logs):
    async def _boom(request: Request):
        raise RuntimeError(SECRET_FRAGMENT)

    temp_route("/__internal_boom_unhandled", _boom)
    # raise_server_exceptions=False: Starlette's ServerErrorMiddleware
    # sends the handler's 500 response and then re-raises so servers
    # can log the error; TestClient would re-raise it into the test.
    # The response body is what the client actually saw.
    client = TestClient(api_main.app, raise_server_exceptions=False)
    resp = client.get("/__internal_boom_unhandled")
    assert resp.status_code == 500
    assert resp.json() == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in resp.text
    rid = resp.headers.get("x-request-id")
    assert rid, "every 500 carries the request id"
    assert any(
        SECRET_FRAGMENT in r.getMessage() and rid in r.getMessage()
        for r in error_logs.records
    )


def test_internal_error_end_to_end_logs_context(client, temp_route, error_logs):
    async def _boom(request: Request):
        raise InternalError(RuntimeError(SECRET_FRAGMENT), context="boom-ctx")

    temp_route("/__internal_boom_internal", _boom)
    resp = client.get("/__internal_boom_internal")
    assert resp.status_code == 500
    assert resp.json() == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in resp.text
    assert any(
        SECRET_FRAGMENT in r.getMessage() and "boom-ctx" in r.getMessage()
        for r in error_logs.records
    )


def test_plain_500_route_end_to_end_is_scrubbed(client, temp_route, error_logs):
    """The safety net end-to-end: a route raising a plain
    HTTPException(500, detail=str(e)) still cannot leak."""

    async def _boom(request: Request):
        raise HTTPException(status_code=500, detail=SECRET_FRAGMENT)

    temp_route("/__internal_boom_plain", _boom)
    resp = client.get("/__internal_boom_plain")
    assert resp.status_code == 500
    assert resp.json() == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in resp.text


def test_swept_route_500_body_is_scrubbed(
    client, monkeypatch, tmp_path, error_logs
):
    """A real swept route (notables paste, now raising InternalError)
    answers a mid-handler DB failure with the generic detail — the
    pre-F1 response body was str(e), i.e. the DB error text."""
    import db.models as db_models

    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(tmp_path / "quarantine"))

    def _boom():
        raise RuntimeError(SECRET_FRAGMENT)

    monkeypatch.setattr(db_models, "SessionLocal", _boom)
    resp = client.post(
        "/api/notables/paste",
        json={"raw_text": "Notable\n\nTitle: t\nHost: h\n"},
    )
    assert resp.status_code == 500
    assert resp.json() == {"detail": INTERNAL_ERROR_DETAIL}
    assert SECRET_FRAGMENT not in resp.text
    assert "x-request-id" in resp.headers
    assert any(SECRET_FRAGMENT in r.getMessage() for r in error_logs.records)


def test_unknown_route_404_detail_intact(client):
    """The sweep must not over-scrub: ordinary 404s keep FastAPI's
    user-relevant detail."""
    resp = client.get("/api/definitely-not-a-route")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Not Found"


# ---------------------------------------------------------------------------
# Request-id middleware
# ---------------------------------------------------------------------------

def test_every_response_carries_generated_request_id(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid
    assert len(rid) == 16 and int(rid, 16) >= 0  # secrets.token_hex(8)


def test_client_supplied_request_id_is_echoed(client):
    resp = client.get("/api/health", headers={"X-Request-Id": "my-rid-42"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "my-rid-42"
