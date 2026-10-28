"""Central internal-error handling (API security review finding F1).

Route handlers must never echo raw exception text to the client:
SQLAlchemy/psycopg messages carry SQL fragments, table names and
host/port; Ollama errors can carry OLLAMA_URL. Every 500 therefore
answers with a generic ``{"detail": "Internal error"}`` while the
real cause is logged server-side, tagged with the request id that
the request-id middleware (api.main) stamps onto every request and
echoes back in the ``X-Request-Id`` response header — so a client
can reference a failure without learning anything from it.

Routes raise :class:`InternalError` (a 500 ``HTTPException`` that
carries the real cause for the log). The 500 exception handler in
api.main scrubs the detail of ANY plain 500 ``HTTPException`` as a
safety net, so a future ``raise HTTPException(500, detail=str(e))``
still cannot leak. Other 5xx codes pass through untouched (e.g. the
analysis 504 timeout carries a static, user-relevant message).
"""
import logging

from fastapi import HTTPException

logger = logging.getLogger("soc.api")

# The only 500 detail a client ever sees.
INTERNAL_ERROR_DETAIL = "Internal error"


def request_id(request) -> str:
    """The request id stamped by the request-id middleware (or "?")."""
    return getattr(getattr(request, "state", None), "request_id", None) or "?"


def log_internal_error(request, cause, context: str = "") -> None:
    """Log the real cause of an internal error, tagged with the
    request id so the server-side log and the client's X-Request-Id
    header correlate. `cause` may be an exception (logged with its
    traceback) or a string (e.g. a tool's captured output)."""
    rid = request_id(request)
    where = context or (request.url.path if request is not None else "?")
    if isinstance(cause, BaseException):
        logger.error(
            "Internal error [%s] in %s: %s", rid, where, cause, exc_info=cause,
        )
    else:
        logger.error("Internal error [%s] in %s: %s", rid, where, cause)


class InternalError(HTTPException):
    """A 500 whose real cause is for the server log only.

    The client receives the generic detail; the handler in api.main
    logs `cause` (with traceback) under the request id.
    """

    def __init__(self, cause, context: str = ""):
        super().__init__(status_code=500, detail=INTERNAL_ERROR_DETAIL)
        self.cause = cause
        self.context = context


def raise_internal(cause, context: str = ""):
    """Raise :class:`InternalError` — the one-liner for `except`
    blocks: ``raise_internal(e, context="rules")``."""
    raise InternalError(cause, context=context)
