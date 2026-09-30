"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import json
import logging
import os
import re
import sys

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

logger = logging.getLogger("soc.api")
# Router split: shared models live in api.schemas, pure helpers in
# api.helpers.*, and the endpoint clusters in api.routes.*. The names
# re-exported here keep existing callers (including the test suite) working
# against api.main unchanged.
from api.schemas import (  # noqa: F401
    InvestigationEvidenceBatchPayload,
    InvestigationEvidenceEntryPayload,
    JobStatus,
)
from api.helpers.evidence import _evidence_entry_is_valid  # noqa: F401
from api.helpers.phase2 import _build_question_driven_followup_queries  # noqa: F401
from api.helpers.triage_keys import (  # noqa: F401
    TRIAGE_KEY_FIELD_PRIORITY,
    extract_triage_key_fields,
    _field_lookup,
)
from db.util import utcnow_naive as _utcnow  # noqa: F401
from api.routes.analyze import router as _analyze_router
from api.routes.closure import router as _closure_router
from api.routes.code_review import router as _code_review_router
from api.routes.evidence import router as _evidence_router
from api.routes.evidence import save_case_evidence  # noqa: F401
from api.routes.notables import router as _notables_router
from api.routes.promote import derive_triage_confidence  # noqa: F401
from api.routes.promote import router as _promote_router
from api.routes.rules import router as _rules_router
from api.routes.splunk import router as _splunk_router
from api.routes.splunk import _SEARCH_ONE_INFLIGHT  # noqa: F401
from api.routes.tools import router as _tools_router
from api.routes.triage import router as _triage_router

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

# ---------------------------------------------------------------------------
# API-key auth (Phase 5 groundwork: ready to activate).
#
# Setting API_KEY activates gating; leaving it unset keeps local development
# friction free (localhost-only exposure). Coverage (Phase 5):
#   - middleware below: EVERY mutating /api/* request (POST/PUT/PATCH/DELETE)
#     — this is the default, so future routes are gated the moment they exist;
#   - explicit `dependencies=[Depends(require_api_key)]` on dangerous routes
#     in api.routes.* stays as defense in depth.
# Deliberately NOT gated (read-only / health / static UI): /api/health and
# other GETs, the mounted web UI. The frontend stamps X-API-Key on every
# axios request (web/utils/auth.js reads window.SOC_CONFIG.apiKey or
# ?apiKey= for local testing); the server injects window.SOC_CONFIG into
# index.modular.html when the gate is armed (see the /index.modular.html
# route below), so activation is just: put API_KEY in the BWS vault + .env,
# restart. Anyone who can load the UI can read the injected key — that is
# inherent to browser-side auth and acceptable while the UI is only exposed
# on localhost/Tailscale.
# ---------------------------------------------------------------------------
# The key itself lives in api.auth — one patch point for the middleware and
# the require_api_key dependency used across api.routes.*.
from api import auth  # noqa: E402


# ---------------------------------------------------------------------------
# S13: canonical REST resource paths.
#
# The public API prefers resource-oriented paths (/api/cases, /api/notables,
# /api/evidence, /api/analyses); the historical /api/db/* spellings keep
# working as aliases. Rewriting happens at the ASGI layer so the routers and
# handlers stay untouched — one mapping table, zero behavioral drift, and
# auth/CORS middleware see the same method+path shape either way.
# ---------------------------------------------------------------------------
def canonical_to_legacy_path(path):
    """Map a canonical REST path to its legacy /api/db/* route path.

    Returns None when the path is not a canonical spelling (legacy paths,
    health, static UI all pass through untouched).
    """
    if path == "/api/cases":
        return "/api/db/triage"
    if path == "/api/analyses":
        return "/api/db/analyze"

    m = re.match(r"^/api/notables(?:/(?P<rest>.*))?$", path)
    if m:
        rest = m.group("rest")
        return "/api/db/notables" + (("/" + rest) if rest else "")

    m = re.match(r"^/api/cases/(?P<cid>[^/]+)(?P<rest>/.*)?$", path)
    if m:
        # The /api/cases/* tree mirrors /api/db/triage/* 1:1 (detail,
        # notable, investigation-state, closure-readiness, evidence* and its
        # delete subroutes, case delete, batch-delete), so any tail maps
        # straight through; unknown subresources 404 identically either way.
        return "/api/db/triage/%s%s" % (m.group("cid"), m.group("rest") or "")

    m = re.match(r"^/api/evidence/(?P<cid>[^/]+)(?P<rest>/.*)?$", path)
    if m:
        return "/api/db/triage/%s/evidence%s" % (m.group("cid"), m.group("rest") or "")

    return None


class CanonicalPathRewriter:
    """ASGI middleware: canonical resource paths -> legacy /api/db/* routes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            legacy = canonical_to_legacy_path(scope.get("path", ""))
            if legacy:
                scope = dict(scope, path=legacy, raw_path=legacy.encode("ascii"))
        await self.app(scope, receive, send)


app = FastAPI(
    title="SOC Platform API",
    lifespan=lifespan,
    description="REST API for SOC Orchestration Platform tools and workflows with local AI analysis",
    version="1.0.0",
    docs_url="/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _enable_docs else None,
)


@app.middleware("http")
async def _api_key_mutation_gate(request, call_next):
    """Gate /api/* requests per AUTH_MODE (SESSION_AUTH_PLAN.md).

    Flag off: mutating /api/* requests need the armed API key (X-API-Key
    header or ?api_key=) — exactly the pre-C1B behavior, byte-identical.
    AUTH_MODE=session: every /api/* request needs an authenticated actor
    (session cookie, or the API key as the admin machine actor); cookie-backed
    mutations additionally need X-CSRF-Token. /health, /api/health and the
    /api/auth/* endpoints themselves stay reachable for health checks/login.
    """
    path = request.url.path
    if auth.session_mode():
        actor = auth.resolve_actor(request)
        is_api = path.startswith("/api/")
        if is_api and path not in auth.EXEMPT_PATHS and actor.kind == "anonymous":
            return JSONResponse({"detail": "Authentication required"}, status_code=401)
        if auth.mutation_gate_rejects(request) and path not in auth.EXEMPT_PATHS:
            if actor.kind == "anonymous":
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            if actor.kind == "session" and not actor.csrf_ok:
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
    elif auth._API_KEY and auth.mutation_gate_rejects(request) and not auth.request_has_api_key(request):
        return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
    return await call_next(request)

# The hosted branch preview can call a locally running API through a secure
# HTTPS tunnel. Keep the allowlist explicit; do not enable wildcard CORS for
# the SOC data and analysis endpoints.
cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        # The frontend uses exactly these methods and headers (see
        # web/modules/api.js); keep the allowlist tight instead of "*".
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Accept", "X-API-Key"],
    )

# Canonical resource paths rewrite to the legacy /api/db/* routes (S13).
app.add_middleware(CanonicalPathRewriter)

from api.routes.system import router as system_router
app.include_router(system_router)
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
    # Global exception handler for unhandled errors.
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )

# Serve web UI. index.modular.html is served through a small route that
# injects the window.SOC_CONFIG bootstrap (O2) when the API-key mutation
# gate is armed: web/utils/auth.js reads window.SOC_CONFIG.apiKey and stamps
# X-API-Key on every axios request, so UI writes work without ever putting
# the key in the repo. When gating is disarmed the page is served verbatim
# (bootstrap omitted — nothing needs to authenticate). Every other static
# asset, including the index.html redirect shim, stays on the plain mount.
web_dir = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.exists(web_dir):
    @app.get("/index.modular.html", include_in_schema=False)
    def _index_modular():
        index_path = os.path.join(web_dir, "index.modular.html")
        if not os.path.exists(index_path):
            raise HTTPException(status_code=404, detail="UI not found")
        with open(index_path, "r", encoding="utf-8") as fh:
            html = fh.read()
        from api.auth import _API_KEY
        if not _API_KEY:
            return Response(content=html, media_type="text/html",
                            headers={"Cache-Control": "no-cache"})
        # json.dumps keeps arbitrary key bytes safely inside the JS string
        # literal; placed before the first script tag so auth.js's interceptor
        # (loaded later) sees the config.
        bootstrap = "<script>window.SOC_CONFIG = %s;</script>\n" % json.dumps({"apiKey": _API_KEY})
        cut = html.find("<script")
        if cut == -1:
            html = bootstrap + html
        else:
            html = html[:cut] + bootstrap + html[cut:]
        return Response(content=html, media_type="text/html",
                        headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
