"""Triage case surface: list/detail, delete + batch delete.

Also owns `_purge_case_related_records`, the one routine that tears down
everything a case owns beyond its triage row (analysis results, evidence,
investigation states, supportive results, closure notes).
"""
import json
import sys
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from api import deps

from api.helpers.triage_keys import extract_triage_key_fields

router = APIRouter()

def _purge_case_related_records(
    db,
    case_id: str,
    delete_analysis: bool = True,
    unlink_source_notable: bool = True,
) -> Dict[str, Any]:
    """Remove investigation evidence/state (and optionally analysis + source notable links).

    Used when deleting a triage case so re-paste / re-promote is not blocked by
    leftover rows, and so evidence does not orphan against a missing case.
    """
    from db.models import (  # type: ignore
        AnalysisResult,
        SupportiveQueryResult,
        InvestigationState,
        ClosureNote,
        SplunkEvent,
    )

    stats = {
        "evidence_deleted": 0,
        "analysis_deleted": 0,
        "closure_notes_deleted": 0,
        "investigation_state_deleted": 0,
        "source_notables_unlinked": 0,
        "source_notables_deleted": 0,
    }

    stats["evidence_deleted"] = db.query(SupportiveQueryResult).filter(
        SupportiveQueryResult.case_id == case_id
    ).delete(synchronize_session=False)

    stats["investigation_state_deleted"] = db.query(InvestigationState).filter(
        InvestigationState.case_id == case_id
    ).delete(synchronize_session=False)

    if delete_analysis:
        stats["analysis_deleted"] = db.query(AnalysisResult).filter(
            AnalysisResult.case_id == case_id
        ).delete(synchronize_session=False)

    try:
        stats["closure_notes_deleted"] = db.query(ClosureNote).filter(
            ClosureNote.case_id == case_id
        ).delete(synchronize_session=False)
    except Exception:
        pass

    if unlink_source_notable:
        candidate_events = []
        if case_id.startswith("NOTABLE-"):
            try:
                eid = int(case_id.split("-", 1)[1])
                event = db.query(SplunkEvent).filter(
                    SplunkEvent.id == eid,
                    SplunkEvent.sourcetype == "splunk:notable:pasted",
                ).first()
                if event:
                    candidate_events.append(event)
            except (TypeError, ValueError):
                pass

        if not candidate_events:
            pasted = db.query(SplunkEvent).filter(
                SplunkEvent.sourcetype == "splunk:notable:pasted"
            ).all()
            for event in pasted:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    continue
                if payload.get("promoted_case_id") == case_id:
                    candidate_events.append(event)

        for event in candidate_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                payload = {}

            is_historical = bool(payload.get("historical"))
            if is_historical:
                if payload.get("promoted_case_id") == case_id:
                    payload.pop("promoted_case_id", None)
                    payload.pop("promoted_at", None)
                    event.raw = json.dumps(payload)
                    stats["source_notables_unlinked"] += 1
            else:
                # Open working notable: remove so the same paste can be re-added cleanly.
                db.delete(event)
                stats["source_notables_deleted"] += 1

    return stats



@router.get("/api/db/triage", tags=["Database"])
def get_triage(
    limit: int = Query(50, ge=1, le=1000),
    search: str = Query("", description="Search case ID, rule name, or summary"),
    verdict: str = Query("", description="Filter by verdict"),
):
    """Get triaged cases from database."""
    try:
        sys.path.insert(0, deps.get_platform_root())
        from sqlalchemy import or_
        from db.models import SessionLocal, TriageResult

        db = SessionLocal()

        query = db.query(TriageResult)

        normalized_verdict = verdict.strip().lower()
        if normalized_verdict:
            query = query.filter(TriageResult.verdict.ilike(normalized_verdict))

        normalized_search = search.strip()
        if normalized_search:
            search_term = f"%{normalized_search}%"
            query = query.filter(
                or_(
                    TriageResult.case_id.ilike(search_term),
                    TriageResult.rule_name.ilike(search_term),
                    TriageResult.analysis_summary.ilike(search_term)
                )
            )

        results = query.order_by(TriageResult.triaged_at.desc()).limit(limit).all()

        # Batch-load source pasted notables so collapsed cards can show key fields
        # (Host, User, Path, etc.) without requiring an expand/click.
        case_ids = [r.case_id for r in results]
        notable_by_case: Dict[str, Dict[str, Any]] = {}
        if case_ids:
            try:
                from db.models import SplunkEvent
                pasted = db.query(SplunkEvent).filter(
                    SplunkEvent.sourcetype == "splunk:notable:pasted"
                ).order_by(SplunkEvent.ingested_at.desc()).all()
                for event in pasted:
                    try:
                        payload = json.loads(event.raw) if event.raw else {}
                    except Exception:
                        continue
                    promoted = payload.get("promoted_case_id")
                    if promoted and promoted in case_ids and promoted not in notable_by_case:
                        fields = payload.get("fields") or {}
                        raw_fields = payload.get("raw_fields") or fields
                        notable_by_case[promoted] = {
                            "fields": fields,
                            "raw_fields": raw_fields,
                            "key_fields": extract_triage_key_fields(fields, raw_fields),
                        }
            except Exception:
                # Non-fatal: cards still render without key_fields
                pass

        return [
            {
                "case_id": r.case_id,
                "rule_name": r.rule_name,
                "rule_id": r.rule_id,
                "verdict": r.verdict,
                "confidence_score": r.confidence_score,
                "analysis_summary": r.analysis_summary,
                "remediation_steps": r.remediation_steps,
                "triaged_at": r.triaged_at.isoformat() if r.triaged_at else None,
                "from_pasted_notable": r.case_id in notable_by_case,
                "key_fields": (notable_by_case.get(r.case_id) or {}).get("key_fields") or {},
            }
            for r in results
        ]
    except Exception as e:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.get("/api/db/triage/{case_id}", tags=["Database"])
def get_triage_case(case_id: str):
    """Get a specific triage case by case ID."""
    db = None
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, TriageResult

        db = SessionLocal()
        result = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not result:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        return {
            "case_id": result.case_id,
            "rule_name": result.rule_name,
            "rule_id": result.rule_id,
            "verdict": result.verdict,
            "confidence_score": result.confidence_score,
            "analysis_summary": result.analysis_summary,
            "remediation_steps": result.remediation_steps,
            "triaged_at": result.triaged_at.isoformat()
        }
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass



@router.post("/api/db/triage/{case_id}/delete", tags=["Database"])
def delete_triage_case(case_id: str, delete_analysis: bool = Query(False, description="Also delete analysis results for this case")):
    """Delete a triage case from the database.

    Intended mainly for removing test/development cases; this does not
    automatically delete any related analysis results.
    """
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, TriageResult, AnalysisResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Triage case {case_id} not found")

        purge_stats = _purge_case_related_records(
            db,
            case_id,
            delete_analysis=delete_analysis,
            unlink_source_notable=True,
        )
        db.delete(case)
        db.commit()

        return {"success": True, "purged": purge_stats}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@router.post("/api/db/triage/batch-delete", tags=["Database"])
def batch_delete_triage_cases(payload: Dict[str, Any]):
    """Delete multiple triage cases in one call.

    Expects JSON payload:
    {"case_ids": ["CASE-1", "CASE-2", ...], "delete_analysis": true/false}
    """
    try:
        case_ids = payload.get("case_ids") or []
        delete_analysis = bool(payload.get("delete_analysis"))

        if not isinstance(case_ids, list) or not case_ids:
            raise HTTPException(status_code=400, detail="case_ids list is required")

        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, TriageResult, AnalysisResult

        db = SessionLocal()
        deleted: List[str] = []
        missing: List[str] = []

        try:
            for case_id in case_ids:
                cid = (case_id or "").strip()
                if not cid:
                    continue
                case = db.query(TriageResult).filter(TriageResult.case_id == cid).first()
                if not case:
                    missing.append(cid)
                    continue
                _purge_case_related_records(
                    db,
                    cid,
                    delete_analysis=delete_analysis,
                    unlink_source_notable=True,
                )
                db.delete(case)
                deleted.append(cid)

            db.commit()
        finally:
            db.close()

        return {"success": True, "deleted": deleted, "missing": missing}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


