"""Splunk HTTP surface: the boundary latch + one-click search-one.

The boundary (\"the latch\") is the single doorway for Splunk data — see the
banner above the routes. search-one runs one supportive query end-to-end and
ledgers the outcome as `splunk_auto` evidence, with per-case inflight
de-duplication and a hard timeout.
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

from core_lib.utils import get_platform_root
from services import splunk_boundary

from api.auth import require_api_key
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


@router.get("/api/splunk-boundary/status", tags=["System"])
def get_splunk_boundary_status():
    """Inspect the latch: mode, staging, and admitted batches."""
    return splunk_boundary.status()


@router.post("/api/splunk-boundary/admit", tags=["System"], dependencies=[Depends(require_api_key)])
def admit_splunk_file(file: UploadFile = File(...)):
    """Admit a Splunk export through the boundary (validate -> quarantine -> ingest)."""
    if splunk_boundary.current_mode() == "quarantined":
        raise HTTPException(
            status_code=403,
            detail=(
                "Splunk boundary is quarantined: HTTP admission is disabled. "
                "Use host-local ingest tooling, or set SPLUNK_BOUNDARY_MODE=restricted."
            ),
        )
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


@router.delete("/api/splunk-boundary/batches/{batch_id}", tags=["System"], dependencies=[Depends(require_api_key)])
def purge_splunk_batch(batch_id: str):
    """Purge one admitted batch: staged files and its ingested DB rows."""
    try:
        return splunk_boundary.purge_batch(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))



_SEARCH_ONE_TIMEOUT_SECONDS = float(os.environ.get("SEARCH_ONE_TIMEOUT_SECONDS", "60"))
_SEARCH_ONE_INFLIGHT: set = set()
_SEARCH_ONE_INFLIGHT_LOCK = threading.Lock()


@router.post("/api/splunk/search-one", tags=["Splunk"], dependencies=[Depends(require_api_key)])
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
    with _SEARCH_ONE_INFLIGHT_LOCK:
        if case_id in _SEARCH_ONE_INFLIGHT:
            raise HTTPException(
                status_code=409,
                detail="A search is already running for this case (per-case concurrency cap is 1)",
            )
        _SEARCH_ONE_INFLIGHT.add(case_id)

    db = None
    job_id = uuid.uuid4().hex
    try:
        sys.path.insert(0, get_platform_root())
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
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        with _SEARCH_ONE_INFLIGHT_LOCK:
            _SEARCH_ONE_INFLIGHT.discard(case_id)
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


