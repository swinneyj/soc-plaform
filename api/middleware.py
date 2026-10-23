"""HTTP middleware stack for the SOC Platform API (E.5 split out of api/main.py).

Registration ORDER is load-bearing: Starlette wraps middleware in the order
they are added, so the LAST added runs OUTERMOST. `install(app)` replays the
exact historical order — request-id → gate → CORS → canonical-path rewriter →
body cap → rate limit — so behavior is byte-identical to the pre-split app.

Module globals here are intentionally mutable: the test suite clears
`_RATE_HITS` between cases and monkeypatches `RATE_LIMIT_PER_MIN` and
`MAX_BODY_BYTES`. Patch **this** module's attributes for the ints (rebinding
api.main's re-export would not reach the running middleware).
"""

import logging
import os
import re
import secrets
import time

from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import auth
from api.helpers.audit import audit_event

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


# ---------------------------------------------------------------------------
# F1: per-request id — stamped onto request.state, echoed back in the
# X-Request-Id response header, and attached to every server-side internal-
# error log line (api.helpers.errors), so a client can report the id it saw
# and the operator finds the matching traceback without the response body
# ever carrying internal error text.
# ---------------------------------------------------------------------------
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


# C2.1.5 hardening (DEVELOPMENT_PLAN §11, signed off Sept 30): per-client-IP
# sliding 60 s window on the Ollama-backed POST surfaces; over-limit -> 429.
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


async def _body_size_cap(request, call_next):
    """F9: reject bodies over MAX_BODY_BYTES with 413 before parsing.

    A declared Content-Length is checked before reading a single byte.
    Chunked (undeclared) bodies are drained through the raw ASGI receive
    channel with a running total and replayed, so an unbounded stream
    fails at the cap instead of exhausting worker memory. Registered after
    the rate limiter so it runs outermost (rejects first, cheapest).
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


def _cors_origins():
    """Explicit CORS allowlist (env `CORS_ORIGINS`); empty means no middleware.

    The hosted branch preview can call a locally running API through a secure
    HTTPS tunnel. Keep the allowlist explicit; do not enable wildcard CORS for
    the SOC data and analysis endpoints.
    """
    return [
        origin.strip()
        for origin in os.environ.get("CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]


def install(app):
    """Register the whole middleware stack on `app`, in historical order.

    The order below is the pre-split registration sequence, and it matters:
    the last-added middleware is the outermost, and the canonical-path
    rewriter sits *outside* CORS (so CORS sees rewritten paths) and *inside*
    the body cap/rate limiter (so those reject before any work happens).
    """
    app.middleware("http")(_request_id)
    app.middleware("http")(_api_key_mutation_gate)

    origins = _cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            # The frontend uses exactly these methods and headers (see
            # web/modules/api.js); keep the allowlist tight instead of "*".
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Content-Type", "Accept", "X-API-Key"],
        )

    # Canonical resource paths rewrite to the legacy /api/db/* routes (S13).
    app.add_middleware(CanonicalPathRewriter)

    # Registered last => outermost: reject oversized bodies and rate-limit
    # brute-force POST traffic before anything else runs.
    app.middleware("http")(_body_size_cap)
    app.middleware("http")(_ollama_rate_limit)
