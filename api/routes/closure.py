"""Closure endpoints: readiness gating and structured note generation.

The disposition is derived from the evidence-backed investigation state;
operator-supplied values are advisory/audited only (plan §6).
"""

import sys

from fastapi import APIRouter, HTTPException

from core_lib.utils import get_platform_root

router = APIRouter()


@router.get("/api/db/triage/{case_id}/closure-readiness", tags=["Rules"])
def check_closure_readiness(case_id: str):
    """Evaluate whether a case satisfies all investigation gating rules for closure."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, InvestigationState
        from services.investigation_state import _serialize_investigation_state_record
        from services.closure_service import evaluate_closure_readiness

        db = SessionLocal()
        inv = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        state_payload = _serialize_investigation_state_record(inv) if inv else {}
        readiness = evaluate_closure_readiness(state_payload)
        db.close()
        return readiness
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate an operator-ready structured closure note backed by investigation state and evidence.

    Judgment-flow rule (plan §6): the note's disposition is derived from the
    evidence-backed investigation state. A caller-supplied `disposition` is
    advisory/audited only (echoed as operator_disposition with a
    disposition_conflict flag) and never overrides the derived conclusion.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal
        from services.closure_service import generate_structured_closure_note

        rule_id = request.get("rule_id")
        case_id = request.get("case_id")
        field_values = request.get("field_values", {})
        analyst_notes = request.get("analyst_notes", "")
        disposition = request.get("disposition", "Undetermined")
        force_closure = bool(request.get("force_closure", False))

        if not case_id:
            raise HTTPException(status_code=400, detail="case_id is required")

        db = SessionLocal()
        try:
            return generate_structured_closure_note(
                db,
                case_id=case_id,
                rule_id=rule_id,
                field_values=field_values,
                analyst_notes=analyst_notes,
                disposition=disposition,
                force_closure=force_closure,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

