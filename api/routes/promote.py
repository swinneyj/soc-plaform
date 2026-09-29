"""Promote endpoint: a pasted notable becomes an unverified triage case.

Phase 4 judgment-flow (plan §6): the referring analyst's ES disposition stays
on the notable record for context; the case enters the loop as ``suspicious``
at the 0.80 closure-gate baseline (see derive_triage_confidence).
"""

import json
import sys
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from api import deps
from api.helpers.correlation import _resolve_correlation_rule
from api.helpers.triage_keys import _field_lookup
from db.util import utcnow_naive


router = APIRouter()


def build_triage_analysis_summary(
    title: str,
    fields: Dict[str, Any],
    notable_time: Optional[str] = None,
) -> str:
    """Build a richer analysis_summary string for newly promoted triage cases."""
    summary_parts = [title] if title else []
    disposition = _field_lookup(fields, "disposition")
    if disposition:
        summary_parts.append(f"Disposition: {disposition}")
    status = _field_lookup(fields, "status")
    if status:
        summary_parts.append(f"Status: {status}")
    if notable_time:
        summary_parts.append(f"Time: {notable_time}")
    host = _field_lookup(fields, "host")
    dest = _field_lookup(fields, "destination", "dest")
    if host:
        summary_parts.append(f"Host: {host}")
    if dest and (not host or dest.lower() != host.lower()):
        summary_parts.append(f"Destination: {dest}")
    user = _field_lookup(fields, "user", "username")
    if user:
        summary_parts.append(f"User: {user}")
    urgency = _field_lookup(fields, "urgency")
    if urgency:
        summary_parts.append(f"Urgency: {urgency}")
    path = _field_lookup(fields, "ssh_file_path", "file_path", "file_name")
    if path:
        label = "SSH File Path" if fields.get("ssh_file_path") else ("File Path" if fields.get("file_path") else "File Name")
        # Prefer explicit label when available
        if _field_lookup(fields, "ssh_file_path"):
            label = "SSH File Path"
        elif _field_lookup(fields, "file_path"):
            label = "File Path"
        else:
            label = "File Name"
        summary_parts.append(f"{label}: {path}")
    process = _field_lookup(fields, "process")
    if process:
        summary_parts.append(f"Process: {process}")
    parent = _field_lookup(fields, "parent_process")
    if parent:
        summary_parts.append(f"Parent Process: {parent}")
    return " | ".join(summary_parts)


def derive_triage_confidence(disposition: str) -> float:
    """Closed-loop baseline confidence for a newly promoted case.

    Phase 4 (judgment-flow audit): every promoted notable is an unverified
    alert. The referring analyst's ES disposition is a pre-loop execution
    fact — recorded on the notable for context, never scored — so the
    baseline no longer depends on it. Cases start exactly at the 0.80
    closure gate: earned evidence closes them naturally, while refuting or
    missing evidence keeps them gated below it (the confidence engine in
    services/investigation_state.py can only add ~+0.26 total).
    """
    return 0.8


@router.post("/api/db/notables/{event_id}/promote", tags=["Database"])
def promote_notable_to_triage(event_id: int):
    """Promote a pasted notable into the triage_results table."""
    try:
        sys.path.insert(0, deps.get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult, ESCorrelationRule

        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).first()

        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")

        try:
            payload = json.loads(event.raw) if event.raw else {}
        except Exception:
            payload = {}

        fields = payload.get("fields", {})
        existing_case_id = payload.get("promoted_case_id")
        is_historical = payload.get("historical", False)
        if existing_case_id:
            existing_case = db.query(TriageResult).filter(TriageResult.case_id == existing_case_id).first()
            if existing_case:
                return {
                    "success": True,
                    "already_promoted": True,
                    "case_id": existing_case.case_id,
                    "verdict": existing_case.verdict,
                }

        # Existing promoted cases are still returned, but new promotions for
        # historical (closed) notables are blocked.
        if is_historical and not existing_case_id:
            raise HTTPException(
                status_code=400,
                detail="Historical (closed) pasted notables are stored for reference but are not promoted into triage."
            )

        case_id = existing_case_id or f"NOTABLE-{event.id}"
        if db.query(TriageResult).filter(TriageResult.case_id == case_id).first():
            raise HTTPException(status_code=409, detail=f"Case ID {case_id} already exists")

        title = fields.get("title") or event.source or f"Pasted notable {event.id}"
        correlation_search = fields.get("correlation_search") or title
        # Phase 4 (judgment-flow audit): the pasted ES disposition is the
        # referring analyst's conclusion — kept on the notable record for
        # context, never promoted into the case. Verdict is fixed at
        # "suspicious" (an alert is, by definition, unverified) and the
        # investigation loop derives the real verdict from the evidence
        # ledger; confidence starts at the closure-gate baseline (see
        # derive_triage_confidence).
        verdict = "suspicious"
        confidence = derive_triage_confidence("")
        notable_time = fields.get("time") or (event.timestamp.isoformat() if event.timestamp else None)

        # Try to resolve a stable rule_id from the ES correlation rules
        # table so future cases for the same rule consistently reuse the
        # same supportive queries and templates.
        resolved_rule = _resolve_correlation_rule(db, ESCorrelationRule, correlation_search)
        resolved_rule_id = resolved_rule.rule_id if resolved_rule else None

        analysis_summary = build_triage_analysis_summary(title, fields, notable_time)

        remediation_steps = "Review the sanitized notable evidence, validate disposition, and gather any supporting host/user activity before closure."

        triage_case = TriageResult(
            case_id=case_id,
            rule_name=correlation_search,
            rule_id=resolved_rule_id,
            verdict=verdict,
            confidence_score=confidence,
            analysis_summary=analysis_summary,
            remediation_steps=remediation_steps,
            triaged_at=event.timestamp or utcnow_naive(),
        )
        db.add(triage_case)

        payload["promoted_case_id"] = case_id
        payload["promoted_at"] = utcnow_naive().isoformat()
        event.raw = json.dumps(payload)

        db.commit()

        return {
            "success": True,
            "already_promoted": False,
            "case_id": case_id,
            "verdict": verdict,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


