"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from api.helpers.errors import (
    INTERNAL_ERROR_DETAIL,
    log_internal_error,
    request_id,
)
from api.helpers.audit import audit_event
import json
import logging
import os
import re
import secrets
import sys
import time

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


# ---------------------------------------------------------------------------
# F1: per-request id — stamped onto request.state, echoed back in the
# X-Request-Id response header, and attached to every server-side internal-
# error log line (api.helpers.errors), so a client can report the id it saw
# and the operator finds the matching traceback without the response body
# ever carrying internal error text.
# ---------------------------------------------------------------------------
@app.middleware("http")
async def _request_id(request, call_next):
    rid = request.headers.get("X-Request-Id") or secrets.token_hex(8)
    request.state.request_id = rid
    response = await call_next(request)
    response.headers["X-Request-Id"] = rid
    # F13: baseline security headers. nosniff is safe everywhere (the UI
    # serves no sniff-dependent content); no-store is scoped to /api/* so
    # static-asset caching is untouched while authenticated JSON (case
    # data included) is never written to the browser cache.
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _gate_reject(request, status: int, detail: str, reason: str, actor=None):
    """Audit a gate rejection and answer it (task 3 security log).

    This middleware is registered AFTER _request_id, so on this path
    (which returns without calling downstream) request.state.request_id
    does not exist yet: stamp it here and echo it in X-Request-Id so a
    rejected client can still correlate with the audit line.
    """
    rid = request.headers.get("X-Request-Id") or secrets.token_hex(8)
    request.state.request_id = rid
    audit_event(
        "auth_rejected", request, level=logging.WARNING,
        status=status, reason=reason,
        actor=actor.kind if actor else "anonymous",
        actor_role=(actor.role or None) if actor else None,
        username=getattr(actor.user, "username", None) if actor else None,
    )
    return JSONResponse(
        {"detail": detail}, status_code=status,
        headers={"X-Request-Id": rid},
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

    Task 3 (security audit stream): every rejection is audited
    (auth_rejected), and every SUCCESSFUL mutating /api/* request is
    audited as a state_change attributed to its actor — see the tail of
    this function. /api/auth/* are EXEMPT_PATHS: login/logout carry
    their own dedicated events in api.routes.auth instead.
    """
    path = request.url.path
    if auth.session_mode():
        actor = auth.resolve_actor(request)
        is_api = path.startswith("/api/")
        if is_api and path not in auth.EXEMPT_PATHS and actor.kind == "anonymous":
            return _gate_reject(request, 401, "Authentication required", "anonymous")
        if auth.mutation_gate_rejects(request) and path not in auth.EXEMPT_PATHS:
            if actor.kind == "anonymous":
                return _gate_reject(request, 401, "Invalid or missing API key", "anonymous")
            if actor.kind == "session" and not actor.csrf_ok:
                return _gate_reject(
                    request, 403, "CSRF token missing or invalid", "csrf", actor=actor)
    elif auth._API_KEY and auth.mutation_gate_rejects(request) and not auth.request_has_api_key(request):
        return _gate_reject(request, 401, "Invalid or missing API key", "missing_api_key")
    # Task 3: attribute every successful state-changing /api/* request
    # to its actor. Resolve BEFORE call_next — at request time — because
    # the handler can end the very session it runs under (logout), which
    # would otherwise audit as anonymous. resolve_actor is cached in
    # session mode (30 s TTL); in API-key mode it is a header comparison.
    will_audit = (
        auth.mutation_gate_rejects(request)
        and path not in auth.EXEMPT_PATHS
    )
    state_actor = None
    if will_audit:
        try:
            state_actor = auth.resolve_actor(request)
        except Exception:
            state_actor = None
    response = await call_next(request)
    if will_audit and response.status_code < 400:
        audit_event(
            "state_change", request,
            status=response.status_code,
            actor=state_actor.kind if state_actor else "unknown",
            actor_role=(state_actor.role or None) if state_actor else None,
            username=getattr(state_actor.user, "username", None) if state_actor else None,
        )
    return response

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

# C2.1.5 hardening (DEVELOPMENT_PLAN §11, signed off Sept 30): per-client-IP
# sliding 60 s window on the Ollama-backed POST surfaces; over-limit -> 429.
# Defined after the rewriter registration so this middleware is outermost and
# sees the request's PRE-REWRITE path; the list carries both spellings anyway.
_RATE_HITS: dict = {}  # client ip -> [timestamps]
RATE_LIMIT_PER_MIN = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
# F9: global request-body backstop (env bytes, default 10 MiB). The paste
# endpoint keeps its own tighter 5 MiB gate inside; this caps every other
# JSON endpoint (evidence-batch, notables, rules-import, ...) before parsing.
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(10 * 1024 * 1024)))
_RATE_PATHS = (
    "/api/db/analyze", "/api/analyses",
    "/api/splunk/search-one",
    "/api/db/supportive-queries/draft",
    "/api/code-review",
)


@app.middleware("http")
async def _body_size_cap(request, call_next):
    """F9: reject bodies over MAX_BODY_BYTES with 413 before parsing.

    A declared Content-Length is checked before reading a single byte.
    Chunked (undeclared) bodies are drained through the raw ASGI receive
    channel with a running total and replayed, so an unbounded stream
    fails at the cap instead of exhausting worker memory. Defined after
    _ollama_rate_limit so it runs outermost (rejects first, cheapest).
    """
    if request.method in ("POST", "PUT", "PATCH"):
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared.strip()) > MAX_BODY_BYTES:
                    return JSONResponse(
                        {"detail": "Request body too large"}, status_code=413)
            except (ValueError, AttributeError):
                pass  # malformed header: fall through to the bounded drain
        else:
            buffered = []
            total = 0
            receive = request._receive  # raw ASGI channel, as stream() does
            while True:
                message = await receive()
                buffered.append(message)
                if message["type"] == "http.request":
                    total += len(message.get("body", b""))
                    if total > MAX_BODY_BYTES:
                        return JSONResponse(
                            {"detail": "Request body too large"}, status_code=413)
                    if not message.get("more_body"):
                        break
                elif message["type"] == "http.disconnect":
                    break
            messages = list(buffered)

            async def _replay():
                if messages:
                    return messages.pop(0)
                return {"type": "http.disconnect"}

            request._receive = _replay
    return await call_next(request)


@app.middleware("http")
async def _ollama_rate_limit(request, call_next):
    if request.method == "POST" and any(request.url.path.startswith(p) for p in _RATE_PATHS):
        ip = request.client.host if request.client else "?"
        now = time.time()
        # F8: opportunistic global sweep — drop every IP whose window is
        # fully stale on each limited request. Dict size is then capped at
        # the distinct IPs seen in the last 60 s, so rotating source IPs
        # cannot grow it without bound (a per-IP pop alone would leave
        # one stale entry per never-again-seen IP forever).
        for key in [k for k, v in _RATE_HITS.items()
                    if not any(now - t < 60.0 for t in v)]:
            del _RATE_HITS[key]
        hits = [t for t in _RATE_HITS.get(ip, []) if now - t < 60.0]
        if len(hits) >= RATE_LIMIT_PER_MIN:
            _RATE_HITS[ip] = hits
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
        hits.append(now)
        _RATE_HITS[ip] = hits
    return await call_next(request)

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

# Serve web UI. index.modular.html is served through a small route that
# injects the window.SOC_CONFIG bootstrap (O2) when the API-key mutation
# gate is armed: web/utils/auth.js reads window.SOC_CONFIG.apiKey and stamps
# X-API-Key on every axios request, so UI writes work without ever putting
# the key in the repo. When gating is disarmed the page is served verbatim
# (bootstrap omitted — nothing needs to authenticate). Every other static
# asset, including the index.html redirect shim, stays on the plain mount.
web_dir = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.exists(web_dir):
    # Optimization #2 (API security review): the page used to be
    # re-read from disk and re-injected with the SOC_CONFIG
    # bootstrap on EVERY page load. The rendered HTML is cached
    # keyed on (API_KEY, file mtime): a page load is a cache
    # lookup plus one stat, and the file is re-read + bootstrap
    # re-injected only when the key changes — the file was edited
    # on disk, or the gate armed/disarmed with a different key.
    # The launcher runs a single worker, so a plain dict needs
    # no lock.
    _index_cache = {"key": None, "html": None}  # (api_key, mtime) -> html

    @app.get("/index.modular.html", include_in_schema=False)
    def _index_modular():
        index_path = os.path.join(web_dir, "index.modular.html")
        try:
            mtime = os.stat(index_path).st_mtime
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="UI not found")
        from api.auth import _API_KEY
        cache_key = (_API_KEY, mtime)
        if _index_cache["key"] != cache_key:
            with open(index_path, "r", encoding="utf-8") as fh:
                html = fh.read()
            if _API_KEY:
                # json.dumps keeps arbitrary key bytes safely inside
                # the JS string literal; placed before the first
                # script tag so auth.js's interceptor (loaded later)
                # sees the config.
                bootstrap = "<script>window.SOC_CONFIG = %s;</script>\n" % json.dumps(
                    {"apiKey": _API_KEY}
                )
                cut = html.find("<script")
                if cut == -1:
                    html = bootstrap + html
                else:
                    html = html[:cut] + bootstrap + html[cut:]
            _index_cache["key"] = cache_key
            _index_cache["html"] = html
        return Response(content=_index_cache["html"], media_type="text/html",
                        headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

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
