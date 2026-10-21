"""Structured JSON security-event audit log.

A dedicated logger (``soc.audit``) with a stdlib JSON-line formatter on
stdout, deliberately SEPARATE from the app's ``soc.api`` logger: this
stream carries exactly the security events — login attempts, HTTP
401/403 authorization failures, and state-changing requests — and
nothing else.

Two design decisions worth knowing:

* **Stdlib only** (no python-json-logger): requirements.txt is
  hash-locked, so every new dependency is a pin-regeneration plus
  supply-chain surface; the audit payload is a tiny fixed schema, so
  ``json.dumps`` in a ``logging.Formatter`` covers it with zero deps —
  consistent with the stdlib-KDF choice in api.auth.
* **Two-way field allowlist, not a blocklist**: ``audit_event`` accepts
  ONLY the named fields in AUDIT_FIELDS and raises ValueError for
  anything else (and for the credential/payload names in
  DENIED_FIELDS). Request bodies, raw database/Splunk queries, cookies,
  CSRF tokens, API keys and passwords can therefore never reach the
  audit stream — a future caller passing ``request.json()`` or
  ``headers`` fails loudly in tests instead of silently leaking.
  String values are also truncated, so an attacker-controlled username
  cannot bloat or flood the stream.

Event vocabulary (kept small and stable):

* ``login_success`` / ``login_failure`` / ``login_throttled`` — api.routes.auth
* ``auth_rejected`` — 401/403 authorization failures: the gate middleware
  (anonymous / missing api key / CSRF) and route-level dependencies
  (``require_api_key``, ``require_role``) via the 500/401/403 handler in api.main
* ``state_change`` — every successful mutating /api/* request, attributed
  to its actor (session user or API-key machine actor)
* ``logout`` — session terminated

Every event carries the F1 request id when one exists, so audit lines
correlate with the ``X-Request-Id`` response header and with the
server-side internal-error log lines.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

AUDIT_LOGGER_NAME = "soc.audit"

# Handler level: capture everything audit_event emits (INFO successes,
# WARNING failures). The logger itself never propagates to the root
# handlers, so audit lines cannot mix into uvicorn/app log streams.
_LEVEL = getattr(logging, os.environ.get("AUDIT_LOG_LEVEL", "DEBUG").upper(), logging.DEBUG)

AUDIT_FIELDS = frozenset({
    "event",        # stable event name (also the log message)
    "request_id",   # F1 correlation id (X-Request-Id)
    "path",         # request path (never query string: it may carry ?api_key=)
    "method",
    "status",       # HTTP status of the response
    "actor",        # session | api-key | anonymous | unknown
    "actor_role",   # analyst | admin
    "username",     # attempted or authenticated username
    "ip",
    "reason",       # machine-readable rejection/failure reason
})

# Names that must NEVER appear in an audit record — credential material
# or raw payloads. audit_event raises on these (and on any name outside
# AUDIT_FIELDS) instead of dropping silently, so a misuse fails tests.
DENIED_FIELDS = frozenset({
    "password", "api_key", "x_api_key", "token", "cookie", "csrf_token",
    "session", "secret", "authorization", "body", "headers", "raw_text",
    "query", "sql", "data", "payload",
})

# Attacker-controlled fields (e.g. a login username) are truncated so a
# single event can never exceed a sane line size.
_MAX_FIELD_LEN = 512


class JsonFormatter(logging.Formatter):
    """One JSON object per line: envelope (ts/level/logger/message) plus
    the allowlisted audit payload carried on ``record.audit``."""

    def format(self, record):
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(
                timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "audit", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str, separators=(",", ":"))


def _build_logger() -> logging.Logger:
    lg = logging.getLogger(AUDIT_LOGGER_NAME)
    lg.setLevel(_LEVEL)
    if not lg.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        lg.addHandler(handler)
    # Never bubble into root handlers (uvicorn access log, lastResort).
    lg.propagate = False
    return lg


_AUDIT_LOGGER = _build_logger()


def audit_event(event: str, request=None, level: int = logging.INFO, **fields) -> None:
    """Emit one structured security event on the soc.audit stream.

    Only AUDIT_FIELDS may be passed; DENIED_FIELDS and unknown names
    raise ValueError (two-way allowlist). When a ``request`` is given,
    request_id/path/method/ip are filled from it (the path never
    includes the query string, which may carry ?api_key=).
    """
    denied = DENIED_FIELDS.intersection(fields)
    if denied:
        raise ValueError(
            "audit_event: denied fields may never be logged: %r" % sorted(denied))
    unknown = set(fields) - AUDIT_FIELDS
    if unknown:
        raise ValueError(
            "audit_event: fields outside AUDIT_FIELDS: %r" % sorted(unknown))

    payload = {"event": event}
    if request is not None:
        from api.helpers.errors import request_id

        payload["request_id"] = request_id(request)
        payload["path"] = request.url.path
        payload["method"] = request.method
        client = getattr(request, "client", None)
        payload["ip"] = client.host if client else "?"

    for key, value in fields.items():
        if value is None or value == "":
            continue
        if isinstance(value, str) and len(value) > _MAX_FIELD_LEN:
            value = value[:_MAX_FIELD_LEN] + "...(truncated)"
        payload[key] = value

    _AUDIT_LOGGER.log(level, event, extra={"audit": payload})
