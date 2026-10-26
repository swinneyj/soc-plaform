"""Splunk HTTP surface: the boundary latch + one-click search-one.

The boundary (\"the latch\") is the single doorway for Splunk data — see the
banner above the routes. search-one runs one supportive query end-to-end and
ledgers the outcome as `splunk_auto` evidence, with per-case inflight
de-duplication and a hard timeout. The tail of the file adds read-only
search-backend introspection (`/api/splunk/search-status`,
`/api/splunk/saved-searches`) so backend readiness and live saved searches are
inspectable without executing a query.
"""
import concurrent.futures
import json
import logging
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from api import deps
from api.helpers.errors import raise_internal
from api.helpers import admission
from services import splunk_boundary

from api.auth import require_api_key, require_role
from api.schemas import (
    InvestigationEvidenceBatchPayload,
    InvestigationEvidenceEntryPayload,
    JobStatus,
    SplunkSearchOnePayload,
)
from db.util import utcnow_naive
from api.routes.evidence import save_case_evidence

logger = logging.getLogger("soc.api")

router = APIRouter()

# ---------------------------------------------------------------------------
# Splunk boundary ("the latch") — HTTP surface
#
# Splunk is the sensitive system of record; this is the single HTTP doorway
# for its data. In the default `quarantined` mode every endpoint below
# refuses to operate — data enters only via host-local tooling (the folder
# watcher / CSV ingestor CLI). Flipping the latch to `restricted` (an
# explicit, audited config change) enables browser admission behind the
# API-key gate.
# ---------------------------------------------------------------------------


@router.get("/api/splunk-boundary/status", tags=["System"], dependencies=[Depends(require_role("analyst"))])
def get_splunk_boundary_status():
    """Inspect the latch: mode, staging, and admitted batches."""
    return splunk_boundary.status()


@router.post("/api/splunk-boundary/admit", tags=["System"],
             dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
def admit_splunk_file(file: UploadFile = File(...)):
    """Admit a Splunk export through the boundary (validate -> quarantine -> ingest)."""
    rejection = admission.latch_rejection(
        splunk_boundary.current_mode(),
        permitted_modes=("restricted", "open"),
        refusal_detail=(
            "Splunk boundary is quarantined: HTTP admission is disabled. "
            "Use host-local ingest tooling, or set SPLUNK_BOUNDARY_MODE=restricted."
        ),
    )
    if rejection is not None:
        raise rejection
    import tempfile

    suffix = Path(file.filename or "upload").suffix.lower()
    with tempfile.NamedTemporaryFile(prefix="boundary-", suffix=suffix, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = tmp.name
    try:
        result = splunk_boundary.admit_file(
            tmp_path, source_label="http-upload:" + (file.filename or "unknown")
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    if not (result.get("ingest") or {}).get("success", False):
        raise HTTPException(status_code=400, detail=result.get("ingest", {}))
    return result


@router.delete("/api/splunk-boundary/batches/{batch_id}", tags=["System"],
               dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
def purge_splunk_batch(batch_id: str):
    """Purge one admitted batch: staged files and its ingested DB rows."""
    try:
        return splunk_boundary.purge_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))



_SEARCH_ONE_TIMEOUT_SECONDS = float(os.environ.get("SEARCH_ONE_TIMEOUT_SECONDS", "60"))
_SEARCH_ONE_INFLIGHT: set = set()
_SEARCH_ONE_INFLIGHT_LOCK = threading.Lock()


@router.post("/api/splunk/search-one", tags=["Splunk"], dependencies=[Depends(require_api_key), Depends(require_role("analyst"))])
def splunk_search_one(payload: SplunkSearchOnePayload):
    """Run one supportive query end-to-end and ledger the outcome as `splunk_auto` evidence."""
    from services.search_backend import get_search_backend, map_search_outcome, summarize_search_rows
    from services.evidence_service import resolve_source_notable_for_case
    from services.rule_context_service import render_query_template

    case_id = (payload.case_id or "").strip()
    query_title = (payload.query_title or "").strip()
    if not case_id or not query_title:
        raise HTTPException(status_code=400, detail="case_id and query_title are required")

    # Per-case concurrency cap: one running search per case.
    # 409 (not 429): the conflict is with work on THIS case,
    # so retrying another case is fine but re-issuing this
    # one is not — that distinction lives in the helper.
    with _SEARCH_ONE_INFLIGHT_LOCK:
        rejection = admission.concurrency_rejection(
            case_id in _SEARCH_ONE_INFLIGHT,
            label="search",
            resource="case",
            cap=1,
        )
        if rejection is not None:
            raise rejection
        _SEARCH_ONE_INFLIGHT.add(case_id)

    db = None
    job_id = uuid.uuid4().hex
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import (
            PlaceholderAlias,
            SessionLocal,
            SupportiveQuery,
            SupportiveQueryResult,
            ToolRun,
            TriageResult,
        )

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        # 1. Resolve the SPL template (explicit override, else stored playbook).
        template = (payload.spl or "").strip()
        if not template:
            rule_key = (case.rule_id or "").strip()
            candidates = db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_key).all()
            match = next(
                (c for c in candidates if (c.title or "").strip().lower() == query_title.lower()),
                None,
            )
            if match is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No stored SPL template titled '{query_title}' for rule '{rule_key}'",
                )
            template = (match.spl_query or "").strip()
        if not template:
            raise HTTPException(status_code=422, detail="Query template is empty")

        # 2. Substitute $host$/$user$/... placeholders from the notable fields.
        try:
            notable = resolve_source_notable_for_case(db, case_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Case {case_id} has no source notable to substitute placeholders from",
            )
        fields = notable.get("fields") or {}

        custom_aliases = []
        for alias_row in db.query(PlaceholderAlias).all():
            try:
                alias_fields = json.loads(alias_row.fields or "[]")
            except Exception:
                alias_fields = []
            custom_aliases.append({"alias": alias_row.alias, "fields": alias_fields})

        rendered, unresolved = render_query_template(template, fields, custom_aliases)
        if unresolved:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Query template has unresolved placeholders; populate the notable fields or aliases first.",
                    "unresolved_tokens": unresolved,
                    "known_fields": sorted(fields.keys()),
                },
            )

        earliest = (payload.earliest or "-7d").strip() or "-7d"
        latest = (payload.latest or "now").strip() or "now"

        # 3. Audit trail: the executed query text is recorded before the run.
        tool_run = ToolRun(
            job_id=job_id,
            tool_name="splunk_search_one",
            arguments=json.dumps(
                {
                    "case_id": case_id,
                    "query_title": query_title,
                    "template": template,
                    "spl": rendered,
                    "earliest": earliest,
                    "latest": latest,
                }
            ),
            status=JobStatus.RUNNING.value,
        )
        db.add(tool_run)
        db.commit()

        # 4. Execute with the per-run timeout (guardrail: never block a case).
        backend = get_search_backend()
        error = None
        result = None
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(
                backend.execute_for_case,
                case_id,
                case.rule_id or "",
                query_title,
                rendered,
                earliest,
                latest,
            )
            try:
                result = future.result(timeout=_SEARCH_ONE_TIMEOUT_SECONDS)
            except concurrent.futures.TimeoutError:
                future.cancel()
                error = f"Search timed out after {_SEARCH_ONE_TIMEOUT_SECONDS:.0f}s"
        except Exception as exc:
            result = None
            error = (str(exc) or exc.__class__.__name__).strip()
        finally:
            pool.shutdown(wait=False)

        rows: List[Dict[str, Any]] = []
        if result is not None:
            try:
                raw_payload = json.loads(result.get("raw_result") or "{}")
                rows = raw_payload.get("rows") or []
            except Exception:
                error = error or "Unparseable search backend result payload"

        row_count = len(rows)
        result_status = map_search_outcome(row_count, error=error)
        summary = summarize_search_rows(rows, error=error)

        tool_run.status = JobStatus.COMPLETED.value if error is None else JobStatus.FAILED.value
        tool_run.stdout = json.dumps({"result_status": result_status, "row_count": row_count})
        tool_run.stderr = error or ""
        tool_run.exit_code = 0 if error is None else -1
        tool_run.completed_at = utcnow_naive()
        # Re-runs replace the prior auto-run row for the same (case, query).
        db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id,
            SupportiveQueryResult.source_system == "splunk_auto",
            SupportiveQueryResult.query_title == query_title,
        ).delete(synchronize_session=False)
        db.commit()
        db.close()
        db = None

        # 5. Ledger the outcome through the standard evidence-save path so the
        # investigation state rebuilds immediately, labeled `splunk_auto`.
        saved = save_case_evidence(
            case_id,
            InvestigationEvidenceBatchPayload(
                entries=[
                    InvestigationEvidenceEntryPayload(
                        query_title=query_title,
                        query_text=rendered,
                        result_text=summary,
                        analyst_summary="",
                        finding_type=(payload.finding_type or "neutral").strip() or "neutral",
                        question_resolution=(payload.question_resolution or "not_resolved").strip() or "not_resolved",
                        target_questions=[str(t).strip() for t in (payload.target_questions or []) if str(t).strip()],
                        result_status=result_status,
                        source_system="splunk_auto",
                    )
                ],
                source_system="splunk_auto",
                replace_existing=False,
            ),
        )

        return {
            "success": True,
            "job_id": job_id,
            "case_id": case_id,
            "query_title": query_title,
            "spl": rendered,
            "earliest": earliest,
            "latest": latest,
            "backend": backend.__class__.__name__,
            "result_status": result_status,
            "row_count": row_count,
            "rows": rows[:50],
            "error": error,
            "source_system": "splunk_auto",
            "investigation_state": (saved or {}).get("investigation_state"),
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise_internal(e, context="splunk")
    finally:
        with _SEARCH_ONE_INFLIGHT_LOCK:
            _SEARCH_ONE_INFLIGHT.discard(case_id)
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Read-only search-backend introspection (E.3)
#
# The backend choice is configuration (`SEARCH_BACKEND`), and misconfiguration
# used to be discoverable only by running a real query. These two endpoints
# make the posture inspectable without executing anything: `status` reports
# which backend is active, whether it is ready, and the SPL subset the mock
# accepts; `saved-searches` lists the live instance's visible saved searches
# (a convenience for picking an existing SPL). Both are read-only, both sit
# behind the API-key dependency, and neither ever echoes a credential value.
# ---------------------------------------------------------------------------

_SEARCH_SUBSET = (
    "base free-text and key=value terms",
    "| search k=v",
    "| where k OP v (==, =, !=, >, <, >=, <=)",
    "| stats count by field",
    "| head N",
)


def _search_backend_choice() -> str:
    return (os.environ.get("SEARCH_BACKEND", "mock") or "mock").strip().lower()


@router.get("/api/splunk/search-status",
    tags=["Splunk"],
    dependencies=[Depends(require_api_key), Depends(require_role("analyst"))],
)
def splunk_search_status():
    """Describe the active search backend: readiness, scope, never the secret."""
    from services.search_backend import tls_settings

    chosen = _search_backend_choice()
    url_configured = bool((os.environ.get("SPLUNK_URL") or "").strip())
    token_configured = bool((os.environ.get("SPLUNK_TOKEN") or "").strip())
    # TLS posture is part of readiness: a CA bundle pointing at a missing file
    # is a configuration error the rehearsal would otherwise hit mid-search.
    tls = tls_settings()
    if chosen == "mock":
        ready, problem = True, ""
    elif chosen == "splunk":
        ready = url_configured and token_configured and not tls["problem"]
        if url_configured and token_configured and tls["problem"]:
            problem = tls["problem"]
        elif ready:
            problem = ""
        else:
            problem = "SEARCH_BACKEND=splunk requires SPLUNK_URL and SPLUNK_TOKEN"
    else:
        ready = False
        problem = f"Unknown SEARCH_BACKEND '{chosen}' (expected 'mock' or 'splunk')"
    return {
        "backend": chosen,
        "ready": ready,
        "problem": problem,
        "read_only": True,
        # Booleans only — never the URL or token themselves.
        "credentials": {
            "splunk_url_configured": url_configured,
            "splunk_token_configured": token_configured,
        },
        # Booleans only. `verify: false` is the loud opt-out (see
        # SPLUNK_VERIFY_TLS); `ca_bundle_configured` means trust is widened
        # for this connection via SPLUNK_CA_BUNDLE, not disabled.
        "tls": {
            "verify": tls["verify"],
            "verification_disabled": tls["verification_disabled"],
            "ca_bundle_configured": tls["ca_bundle_configured"],
            "ca_bundle_readable": tls["ca_bundle_readable"],
            # Expiry pre-flight: an expired bundle makes ready false (the
            # handshake already fails), a near-expiry one warns without
            # blocking. Both surface here before a query is attempted.
            "ca_bundle_certificates": tls["ca_bundle_certificates"],
            "ca_bundle_expires_at": tls["ca_bundle_expires_at"],
            "ca_bundle_days_remaining": tls["ca_bundle_days_remaining"],
            "ca_bundle_expired": tls["ca_bundle_expired"],
            "ca_bundle_warning": tls["ca_bundle_warning"],
        },
        "timeout_seconds": _SEARCH_ONE_TIMEOUT_SECONDS,
        "supported_spl_subset": list(_SEARCH_SUBSET),
        "scope": "Search jobs only — no notables, no writes, no configuration endpoints.",
    }


@router.get("/api/splunk/saved-searches",
    tags=["Splunk"],
    dependencies=[Depends(require_api_key), Depends(require_role("analyst"))],
)
def splunk_saved_searches():
    """List visible saved searches on the live Splunk instance (read-only)."""
    from services.search_backend import SplunkSearchError, get_search_backend

    chosen = _search_backend_choice()
    if chosen != "splunk":
        raise HTTPException(
            status_code=409,
            detail=(
                "Live saved-search listing needs SEARCH_BACKEND=splunk plus "
                f"SPLUNK_URL/SPLUNK_TOKEN (active backend: '{chosen}')."
            ),
        )
    try:
        backend = get_search_backend()
        items = backend.list_saved_searches()
    except SplunkSearchError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"success": True, "count": len(items), "items": items}


