"""Evidence-ledger endpoints for the investigation loop.

``/api/db/triage/{case_id}/evidence`` (list / save / delete variants) and the
derived ``/investigation-state`` read. The save handler is also invoked
directly by the search-one flow in api/main.py.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from core_lib.utils import get_platform_root
from services.investigation_state import (
    _build_investigation_state,
    _serialize_investigation_state_record,
    _upsert_investigation_state,
)
from api.flow_support import (
    InvestigationEvidenceBatchPayload,
    _evidence_entry_is_valid,
)


def _utcnow():
    """Naive UTC now (platform convention)."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


router = APIRouter()


def _load_supportive_results_for_case(db, case_id: str) -> List[Dict[str, Any]]:
    """Load all saved evidence rows for a case as dicts for investigation-state rebuild."""
    from db.models import SupportiveQueryResult  # type: ignore

    rows = (
        db.query(SupportiveQueryResult)
        .filter(SupportiveQueryResult.case_id == case_id)
        .order_by(SupportiveQueryResult.created_at.desc())
        .all()
    )
    results: List[Dict[str, Any]] = []
    for r in rows:
        try:
            raw = json.loads(r.raw_result) if r.raw_result else None
        except Exception:
            raw = r.raw_result
        results.append({
            "id": r.id,
            "query_title": r.query_title,
            "source_system": r.source_system,
            "raw_result": raw,
            "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
        })
    return results


def _rebuild_investigation_state_from_evidence(db, case, analysis_stage: str = "evidence_only"):
    """Rebuild and upsert investigation loop state from current evidence rows."""
    from db.models import InvestigationState  # type: ignore

    supportive_results = _load_supportive_results_for_case(db, case.case_id)
    previous_state_record = db.query(InvestigationState).filter(
        InvestigationState.case_id == case.case_id
    ).first()
    previous_state_payload = (
        _serialize_investigation_state_record(previous_state_record)
        if previous_state_record
        else {}
    )
    analysis_text = (case.analysis_summary or "").strip()
    investigation_state = _build_investigation_state(
        case,
        analysis_text,
        [],
        supportive_results,
        analysis_stage,
        previous_state_payload,
    )
    _upsert_investigation_state(db, InvestigationState, investigation_state)
    return investigation_state


def _enrich_timeline_with_evidence_ids(db, case_id: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach SupportiveQueryResult ids onto timeline items so the UI can delete them.

    Older investigation_state rows were saved before timeline items included
    ``id``. Without this enrichment, the Delete button stays hidden forever
    for those cases until evidence is re-saved.
    """
    if not state_payload:
        return state_payload

    evidence_summary = state_payload.get("evidence_summary") or {}
    timeline = evidence_summary.get("timeline") or []
    if not timeline:
        return state_payload

    needs_ids = any(not item.get("id") for item in timeline if isinstance(item, dict))
    if not needs_ids:
        return state_payload

    try:
        from db.models import SupportiveQueryResult  # type: ignore

        rows = (
            db.query(SupportiveQueryResult)
            .filter(SupportiveQueryResult.case_id == case_id)
            .order_by(SupportiveQueryResult.created_at.desc())
            .all()
        )
    except Exception:
        return state_payload

    # Map (title, source_system) -> list of ids (newest first)
    by_key: Dict[tuple, list] = {}
    for r in rows:
        key = ((r.query_title or "").strip().lower(), (r.source_system or "").strip().lower())
        by_key.setdefault(key, []).append(r.id)

    for item in timeline:
        if not isinstance(item, dict) or item.get("id"):
            continue
        key = (
            (item.get("title") or "").strip().lower(),
            (item.get("source_system") or "").strip().lower(),
        )
        ids = by_key.get(key) or []
        if ids:
            item["id"] = ids.pop(0)

    evidence_summary["timeline"] = timeline
    state_payload["evidence_summary"] = evidence_summary
    return state_payload


@router.get("/api/db/triage/{case_id}/investigation-state", tags=["Database"])
def get_investigation_state(case_id: str):
    """Return the latest persisted investigation loop state for a case."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, InvestigationState

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        state = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        if state:
            payload = _serialize_investigation_state_record(state)
            return _enrich_timeline_with_evidence_ids(db, case_id, payload)

        return {
            "case_id": case.case_id,
            "rule_id": case.rule_id or "",
            "current_hypothesis": (case.analysis_summary or "").strip(),
            "provisional_disposition": (case.verdict or "undetermined").strip().lower(),
            "disposition_confidence": float(case.confidence_score or 0.0),
            "loop_status": "collecting_evidence",
            "iteration_count": 0,
            "unresolved_questions": [],
            "closure_blockers": ["No persisted investigation state yet. Run analysis to initialize the loop."],
            "recommended_next_actions": [],
            "evidence_summary": {
                "total_items": 0,
                "by_finding": {"supports": 0, "refutes": 0, "neutral": 0},
                "by_source_system": {},
                "recent_titles": [],
            },
            "last_analysis_stage": "initial",
            "updated_at": None,
        }
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@router.get("/api/db/triage/{case_id}/evidence", tags=["Database"])
def list_case_evidence(
    case_id: str,
    source_system: Optional[str] = Query(default=None, description="Optional source/stage filter, e.g. phase2_manual"),
):
    """List saved investigation evidence for a case.

    This uses the existing supportive_query_results table as a durable
    evidence ledger so the analyst's findings can be replayed into future
    analyses without relying on browser-local state.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQueryResult

        db = SessionLocal()
        query = db.query(SupportiveQueryResult).filter(SupportiveQueryResult.case_id == case_id)
        if source_system:
            query = query.filter(SupportiveQueryResult.source_system == source_system)

        rows = query.order_by(SupportiveQueryResult.created_at.asc()).all()
        items = []
        for row in rows:
            try:
                raw_result = json.loads(row.raw_result) if row.raw_result else {}
            except Exception:
                raw_result = {"result_text": row.raw_result}

            items.append({
                "id": row.id,
                "case_id": row.case_id,
                "rule_id": row.rule_id,
                "query_title": row.query_title,
                "source_system": row.source_system,
                "raw_result": raw_result,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@router.post("/api/db/triage/{case_id}/evidence", tags=["Database"])
def save_case_evidence(case_id: str, payload: InvestigationEvidenceBatchPayload):
    """Persist a batch of case-linked investigation evidence.

    The UI uses this for Phase 2 analyst-pasted SPL results so the AI can
    reason over durable evidence on subsequent analyses rather than only the
    current browser prompt state.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult, InvestigationState

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        source_system = (payload.source_system or "phase2_manual").strip() or "phase2_manual"
        valid_entries = [e for e in (payload.entries or []) if _evidence_entry_is_valid(e)]
        if payload.replace_existing and valid_entries:
            db.query(SupportiveQueryResult).filter(
                SupportiveQueryResult.case_id == case_id,
                SupportiveQueryResult.source_system == source_system,
            ).delete()

        try:
            max_id = db.query(func.max(SupportiveQueryResult.id)).scalar() or 0
        except Exception:
            max_id = 0
        next_id = int(max_id) + 1

        saved_count = 0
        for entry in payload.entries:
            title = (entry.query_title or "").strip()
            result_text = (entry.result_text or "").strip()
            analyst_summary = (entry.analyst_summary or "").strip()
            query_text = (entry.query_text or "").strip()
            result_status = (getattr(entry, "result_status", None) or "success").strip().lower()
            # Legacy 'benign_result' was an analyst verdict; persist it as a
            # plain success so direction is derived from AI analysis instead.
            if result_status == "benign_result":
                result_status = "success"

            if not title:
                continue
            if not (result_text or analyst_summary or query_text) and result_status in ("", "success"):
                continue

            raw_result = json.dumps(
                {
                    "query_text": query_text,
                    "result_text": result_text,
                    "analyst_summary": analyst_summary,
                    "finding_type": (entry.finding_type or "neutral").strip() or "neutral",
                    "question_resolution": (entry.question_resolution or "not_resolved").strip() or "not_resolved",
                    "target_questions": [str(q).strip() for q in (entry.target_questions or []) if str(q).strip()],
                    "result_status": result_status,
                    "collection_time": getattr(entry, "collection_time", None) or _utcnow().isoformat(),
                    "source_system": getattr(entry, "source_system", source_system) or source_system,
                },
                ensure_ascii=False,
            )

            db.add(
                SupportiveQueryResult(
                    id=next_id,
                    case_id=case_id,
                    rule_id=case.rule_id or "",
                    query_title=title,
                    source_system=source_system,
                    raw_result=raw_result,
                )
            )
            next_id += 1
            saved_count += 1

        # Session uses autoflush=False, so newly added rows are invisible to
        # subsequent queries until we flush. Without this, the investigation
        # state rebuild runs against stale data (missing the just-saved
        # evidence) and the Evidence Timeline does not update until a later
        # analysis request re-reads after commit.
        db.flush()

        # Rebuild investigation loop state from the latest evidence so the
        # Investigation Loop view reflects saved entries even when the
        # analyst has not rerun AI analysis yet.
        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )

        db.commit()
        return {
            "success": True,
            "saved_count": saved_count,
            "source_system": source_system,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Phase 3: one-click read-only search execution (mock-first).
#
# `POST /api/splunk/search-one` renders a supportive query template against
# the case's notable fields, runs it through the configured SearchBackend
# (mock by default; see services/search_backend.py), maps the job outcome to
# result_status, and saves the result into the evidence ledger with the
# explicit `splunk_auto` label. Guardrails: per-case concurrency cap of 1,
# a hard timeout, and every executed query text recorded in tool_runs.
# ---------------------------------------------------------------------------



@router.delete("/api/db/triage/{case_id}/evidence/{evidence_id}", tags=["Database"])
def delete_case_evidence(case_id: str, evidence_id: int):
    """Delete a single saved investigation evidence item and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        row = db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.id == evidence_id,
            SupportiveQueryResult.case_id == case_id,
        ).first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Evidence {evidence_id} not found for case {case_id}",
            )

        deleted_title = row.query_title
        deleted_source = row.source_system
        db.delete(row)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_id": evidence_id,
            "query_title": deleted_title,
            "source_system": deleted_source,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@router.post("/api/db/triage/{case_id}/evidence/{evidence_id}/delete", tags=["Database"])
def delete_case_evidence_post(case_id: str, evidence_id: int):
    """POST wrapper for environments that disallow DELETE from the browser UI."""
    return delete_case_evidence(case_id=case_id, evidence_id=evidence_id)


@router.post("/api/db/triage/{case_id}/evidence/batch-delete", tags=["Database"])
def delete_case_evidence_batch(case_id: str, payload: dict):
    """Delete multiple saved investigation evidence items by ID and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        raw_ids = payload.get("ids") or payload.get("evidence_ids") or []
        ids = [int(i) for i in raw_ids if i is not None and str(i).isdigit()]
        if not ids:
            return {"success": True, "deleted_ids": []}

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id,
            SupportiveQueryResult.id.in_(ids)
        ).delete(synchronize_session=False)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_ids": ids,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@router.post("/api/db/triage/{case_id}/evidence/delete-all", tags=["Database"])
def delete_all_case_evidence(case_id: str):
    """Delete all saved investigation evidence for a case and reset loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id
        ).delete(synchronize_session=False)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_all": True,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


