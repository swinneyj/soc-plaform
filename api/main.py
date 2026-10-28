"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.

This module is the app shell only (E.5 second pass): the HTTP middleware stack
lives in api/middleware.py, UI serving in api/routes/ui.py, endpoint clusters
in api/routes/*, shared payload models in api.schemas, and pure helpers in
api.helpers.*. The names re-exported here keep existing callers (including the
test suite) working against api.main unchanged.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.responses import JSONResponse
from api.helpers.errors import (
    INTERNAL_ERROR_DETAIL,
    log_internal_error,
    request_id,
)
from api.helpers.audit import audit_event
import logging
import os
import sys

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

logger = logging.getLogger("soc.api")

from api import auth  # noqa: E402
from api import middleware as _middleware  # noqa: E402
from api.schemas import (  # noqa: F401,E402
    InvestigationEvidenceBatchPayload,
    InvestigationEvidenceEntryPayload,
    JobStatus,
)
from api.helpers.evidence import _evidence_entry_is_valid  # noqa: F401,E402
from api.helpers.phase2 import _build_question_driven_followup_queries  # noqa: F401,E402
from api.helpers.triage_keys import (  # noqa: F401,E402
    TRIAGE_KEY_FIELD_PRIORITY,
    extract_triage_key_fields,
    _field_lookup,
)
from db.util import utcnow_naive as _utcnow  # noqa: F401,E402
from api.routes.analyze import router as _analyze_router  # noqa: E402
from api.routes.closure import router as _closure_router  # noqa: E402
from api.routes.code_review import router as _code_review_router  # noqa: E402
from api.routes.evidence import router as _evidence_router  # noqa: E402
from api.routes.evidence import save_case_evidence  # noqa: F401,E402
from api.routes.notables import router as _notables_router  # noqa: E402
from api.routes.promote import derive_triage_confidence  # noqa: F401,E402
from api.routes.promote import router as _promote_router  # noqa: E402
from api.routes.rules import router as _rules_router  # noqa: E402
from api.routes.splunk import router as _splunk_router  # noqa: E402
from api.routes.splunk import _SEARCH_ONE_INFLIGHT  # noqa: F401,E402
from api.routes.system import router as _system_router  # noqa: E402
from api.routes.tools import router as _tools_router  # noqa: E402
from api.routes.triage import router as _triage_router  # noqa: E402
from api.routes import ui as _ui  # noqa: E402

# Middleware + canonical-path re-exports (E.5): tests and callers reach these
# through api.main, so the shell re-exports the same objects. The mutable ones
# (_RATE_HITS, _index_cache) are shared objects, not copies — resetting them
# here affects the live middleware/route. For the ints (RATE_LIMIT_PER_MIN,
# MAX_BODY_BYTES), patch api.middleware directly.
from api.middleware import (  # noqa: F401,E402
    CanonicalPathRewriter,
    MAX_BODY_BYTES,
    RATE_LIMIT_PER_MIN,
    _RATE_HITS,
    _RATE_PATHS,
    _api_key_mutation_gate,
    _body_size_cap,
    _gate_reject,
    _ollama_rate_limit,
    _request_id,
    canonical_to_legacy_path,
)
from api.routes.ui import _index_cache  # noqa: F401,E402

# UI path re-export (compat: tests resolve index.modular.html via api.main).
web_dir = _ui.WEB_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create any missing tables on application startup."""
    # Database connectivity is optional for the hosted shell and health
    # routes.  In Vercel, importing the models must not make startup fail
    # just because DATABASE_URL is absent or temporarily unreachable.
    try:
        from db.models import Base, engine
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        app.state.database_startup_error = str(exc)
    yield


# Interactive API docs are a recon convenience for local development but an
# attack-surface map in production, so they are disabled unless explicitly
# enabled (set ENABLE_DOCS=1 in .env for local development).
_enable_docs = os.environ.get("ENABLE_DOCS", "") == "1"

app = FastAPI(
    title="SOC Platform API",
    lifespan=lifespan,
    description="REST API for SOC Orchestration Platform tools and workflows with local AI analysis",
    version="1.0.0",
    docs_url="/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _enable_docs else None,
)

# The whole HTTP middleware stack, in its historical registration order:
# per-request id, API-key/session gate, CORS, canonical-path rewriter, body
# cap, rate limiter. Semantics and the patch point for mutating config are
# documented in api/middleware.py.
_middleware.install(app)

app.include_router(_system_router)
app.include_router(_analyze_router)
app.include_router(_closure_router)
app.include_router(_evidence_router)
app.include_router(_notables_router)
app.include_router(_promote_router)
app.include_router(_rules_router)
app.include_router(_code_review_router)
app.include_router(_splunk_router)
app.include_router(_tools_router)
app.include_router(_triage_router)

# Session auth routes (SESSION_AUTH_PLAN.md Piece B) — registered before the
# static mount block so /api/auth/* wins over the catch-all StaticFiles mount.
from api.routes.auth import router as auth_router  # noqa: E402
app.include_router(auth_router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # Global exception handler for unhandled errors (F1): the client gets
    # the generic detail; the real exception is logged server-side with the
    # request id and its traceback.
    log_internal_error(request, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": INTERNAL_ERROR_DETAIL},
        headers={"X-Request-Id": request_id(request)},
    )


@app.exception_handler(HTTPException)
async def _http_exception_handler(request, exc):
    # F1 safety net: scrub the detail of ANY 500 raised as a plain
    # HTTPException (route sweep sites raise InternalError, which this
    # also covers — its cause is logged here). Sub-500 details are
    # user-input validation and pass through byte-identical; other 5xx
    # codes (e.g. the analysis 504 timeout) carry static messages.
    if exc.status_code == 500:
        cause = getattr(exc, "cause", None)
        if cause is None:
            cause = exc.detail
        log_internal_error(request, cause, context=getattr(exc, "context", ""))
        return JSONResponse(
            status_code=500,
            content={"detail": INTERNAL_ERROR_DETAIL},
            headers={**(exc.headers or {}), "X-Request-Id": request_id(request)},
        )
    # Task 3: route-level 401/403 (require_api_key / require_role and any
    # explicit raise) are authorization failures — audit them. Gate
    # rejections never reach this handler (the gate answers directly),
    # so there is no double-log. EXEMPT_PATHS excluded: /api/auth/login
    # raises 401 for every bad password and is audited there with more
    # detail; /api/auth/session 401s on every logged-out UI poll.
    if exc.status_code in (401, 403) and request.url.path not in auth.EXEMPT_PATHS:
        try:
            actor = auth.resolve_actor(request)
            actor_kind, actor_role = actor.kind, (actor.role or None)
            username = getattr(actor.user, "username", None)
        except Exception:
            actor_kind, actor_role, username = "unknown", None, None
        audit_event(
            "auth_rejected", request, level=logging.WARNING,
            status=exc.status_code, reason="route_authorization",
            actor=actor_kind, actor_role=actor_role, username=username,
        )
    return await _default_http_exception_handler(request, exc)


# Serve the web UI (index route with the SOC_CONFIG bootstrap when the gate is
# armed + the catch-all static mount). Must run AFTER include_router so /api/*
# routes win over the "/" mount.
_ui.mount(app)

if __name__ == "__main__":
    import uvicorn
    # F7: loopback by default — LAN/Tailscale exposure must be a deliberate
    # API_BIND=0.0.0.0 choice, never a forgotten default. scripts/dev
    # already launches on 127.0.0.1; the Dockerfile keeps an explicit
    # 0.0.0.0 because container port-mapping requires it there.
    uvicorn.run(
        app,
        host=os.environ.get("API_BIND", "127.0.0.1"),
        port=int(os.environ.get("SOC_PORT", "8000")),
    )
