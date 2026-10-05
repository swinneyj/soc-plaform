"""Closure endpoints: readiness gating and structured note generation.

The disposition is derived from the evidence-backed investigation state;
operator-supplied values are advisory/audited only (plan §6).
"""

import re
import sys

from fastapi import APIRouter, HTTPException

from api import deps
from api.helpers.errors import raise_internal

router = APIRouter()


# Maps each known closure-blocker phrasing to the analysis-tab stage that
# resolves it, so the UI punch list can deep-link the analyst straight to
# the work instead of leaving them to decode the blocker text.
_BLOCKER_ACTION_RULES = [
    (re.compile(r"no persisted investigation state", re.I), 1, "Run initial analysis"),
    (re.compile(r"no saved investigative evidence", re.I), 1, "Run initial analysis"),
    (re.compile(r"query execution failed", re.I), 2, "Fix failed queries"),
    (re.compile(r"telemetry unavailable", re.I), 2, "Address data gaps"),
    (re.compile(r"marked neutral only", re.I), 4, "Re-assess evidence"),
    (re.compile(r"conflicting evidence", re.I), 4, "Resolve conflicting evidence"),
    (re.compile(r"disposition is tentative", re.I), 4, "Gather conclusive evidence"),
    (re.compile(r"remain unresolved", re.I), 4, "Resolve open questions"),
]


def classify_blocker_actions(blockers):
    """Return one {stage, label} routing action per blocker string.

    Unknown phrasings fall back to stage 4 (the follow-up-analysis stage),
    which is where any not-yet-classified investigation work continues.
    """
    actions = []
    for blocker in blockers:
        text = str(blocker)
        for pattern, stage, label in _BLOCKER_ACTION_RULES:
            if pattern.search(text):
                actions.append({"stage": stage, "label": label})
                break
        else:
            actions.append({"stage": 4, "label": "Continue investigation"})
    return actions


@router.get("/api/db/triage/{case_id}/closure-readiness", tags=["Rules"])
def check_closure_readiness(case_id: str):
    """Evaluate whether a case satisfies all investigation gating rules for closure."""
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, InvestigationState
        from services.investigation_state import _serialize_investigation_state_record
        from services.closure_service import evaluate_closure_readiness

        db = SessionLocal()
        inv = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        state_payload = _serialize_investigation_state_record(inv) if inv else {}
        readiness = evaluate_closure_readiness(state_payload)
        db.close()
        if isinstance(readiness, dict):
            readiness["blocker_actions"] = classify_blocker_actions(readiness.get("blockers") or [])
        return readiness
    except Exception as e:
        raise_internal(e, context="closure")


@router.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate an operator-ready structured closure note backed by investigation state and evidence.

    Judgment-flow rule (plan §6): the note's disposition is derived from the
    evidence-backed investigation state. A caller-supplied `disposition` is
    advisory/audited only (echoed as operator_disposition with a
    disposition_conflict flag) and never overrides the derived conclusion.
    """
    # stage_models["closure"] reserved (C3) — this handler threads no model today.
    try:
        sys.path.insert(0, deps.get_platform_root())
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
        raise_internal(e, context="closure")

