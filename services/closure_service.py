"""Closure workflow, evidence ledger compilation, and closure note generation service.

Enforces investigation closure gating, builds comprehensive evidence ledgers,
preserves analysis audit trails, and formats operator-ready structured closure notes.
"""

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Optional, Tuple

NL = chr(10)
NL2 = chr(10) + chr(10)


def evaluate_closure_readiness(investigation_state: Dict[str, Any]) -> Dict[str, Any]:
    """Check whether a case satisfies all gating rules to be closed."""
    if not investigation_state:
        return {
            "is_ready": False,
            "status": "collecting_evidence",
            "confidence": 0.0,
            "provisional_disposition": "undetermined",
            "blockers": ["No investigation state found for this case."],
            "warnings": [],
        }

    status = (investigation_state.get("loop_status") or "collecting_evidence").strip().lower()
    confidence = float(investigation_state.get("disposition_confidence") or 0.0)
    disposition = (investigation_state.get("provisional_disposition") or "undetermined").strip().lower()
    blockers = investigation_state.get("closure_blockers") or []
    unresolved_questions = investigation_state.get("unresolved_questions") or []
    evidence_summary = investigation_state.get("evidence_summary") or {}
    substantive_items = int(evidence_summary.get("substantive_items") or 0)

    warnings: List[str] = []

    if disposition in {"undetermined", "suspicious"}:
        warnings.append(f"Disposition '{disposition}' is tentative. Definitive verdict required for clean closure.")
    if confidence < 0.80:
        warnings.append(f"Disposition confidence ({confidence * 100:.0f}%) is below the 80% closure threshold.")
    if substantive_items < 2:
        warnings.append(f"Only {substantive_items} substantive evidence item(s) recorded; minimum 2 required.")
    if unresolved_questions:
        warnings.append(f"{len(unresolved_questions)} investigative question(s) remain open.")

    is_ready = status == "ready_for_closure" and len(blockers) == 0 and len(warnings) == 0

    return {
        "is_ready": is_ready,
        "status": status,
        "confidence": round(confidence, 3),
        "provisional_disposition": disposition,
        "blockers": blockers,
        "warnings": warnings,
    }


def format_evidence_ledger(supportive_rows: List[Any]) -> str:
    """Format an auditable markdown ledger of all investigative evidence items."""
    if not supportive_rows:
        return f"_No investigative evidence items recorded._{NL}"

    lines = [
        "| # | Query / Check | Source | Result Status | Direction | Key Observation / Finding |",
        "|---|---|---|---|---|---|",
    ]

    for idx, row in enumerate(supportive_rows, 1):
        title = (getattr(row, "query_title", "") or f"Evidence #{idx}").strip()
        source = (getattr(row, "source_system", "splunk") or "splunk").strip()

        raw = getattr(row, "raw_result", {})
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {"result_text": raw}
        if not isinstance(raw, dict):
            raw = {}

        result_status = (raw.get("result_status") or "success").strip().replace("_", " ").title()
        finding_type = (raw.get("finding_type") or "neutral").strip().title()

        observation = (raw.get("analyst_summary") or raw.get("result_text") or "Recorded in ledger").strip()
        clean_obs = re.sub(r"\s+", " ", observation).replace("|", "/")[:140]

        lines.append(f"| {idx} | {title} | {source} | {result_status} | {finding_type} | {clean_obs} |")

    return NL.join(lines) + NL


def format_iteration_audit_trail(analysis_results: List[Any]) -> str:
    """Format an auditable summary of every AI analysis iteration run on the case."""
    if not analysis_results:
        return f"_No prior AI analysis iterations logged._{NL}"

    lines = [
        f"**Total AI Analysis Iterations:** {len(analysis_results)}{NL}",
        "| Iteration | Model | Timestamp | Confidence | Key Hypothesis Takeaway |",
        "|---|---|---|---|---|",
    ]

    for idx, res in enumerate(reversed(analysis_results), 1):
        model = getattr(res, "model_name", "llama3.1:8b") or "llama3.1:8b"
        created_at = getattr(res, "created_at", None)
        created_str = created_at.strftime("%Y-%m-%d %H:%M:%S") if hasattr(created_at, "strftime") else "—"
        conf = float(getattr(res, "confidence", 0.0) or 0.0)

        analysis_body = (getattr(res, "analysis", "") or "").strip()
        first_line = analysis_body.splitlines()[0] if analysis_body else "Analysis executed"
        first_line_clean = re.sub(r"^[#\s*]+", "", first_line).replace("|", "/")[:90]

        lines.append(f"| {idx} | {model} | {created_str} | {conf * 100:.0f}% | {first_line_clean} |")

    return NL.join(lines) + NL


def generate_structured_closure_note(
    db,
    case_id: str,
    rule_id: Optional[str],
    field_values: Dict[str, Any],
    analyst_notes: str,
    disposition: str,
    force_closure: bool = False,
) -> Dict[str, Any]:
    """Compile and persist an operator-ready structured closure note."""
    from db.models import (  # type: ignore
        AnalysisResult,
        ClosureNote,
        ESCorrelationRule,
        InvestigationState,
        SupportiveQueryResult,
        TriageResult,
    )

    case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
    if not case:
        raise KeyError(f"Case {case_id} not found")

    target_rule_id = rule_id or case.rule_id
    rule = None
    if target_rule_id:
        rule = db.query(ESCorrelationRule).filter(ESCorrelationRule.rule_id == target_rule_id).first()

    # Load investigation state
    inv_record = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
    from services.investigation_state import _serialize_investigation_state_record
    inv_state = _serialize_investigation_state_record(inv_record) if inv_record else {}

    readiness = evaluate_closure_readiness(inv_state)

    if not readiness["is_ready"] and not force_closure:
        return {
            "success": False,
            "blocked": True,
            "case_id": case_id,
            "readiness": readiness,
            "message": "Closure blocked: investigation state does not meet closure criteria. Review blockers or force closure.",
        }

    evidence_rows = db.query(SupportiveQueryResult).filter(
        SupportiveQueryResult.case_id == case_id
    ).order_by(SupportiveQueryResult.created_at.asc()).all()

    analyses = db.query(AnalysisResult).filter(
        AnalysisResult.case_id == case_id
    ).order_by(AnalysisResult.created_at.asc()).all()

    now = datetime.datetime.utcnow()
    date_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    rule_name = rule.rule_name if rule else (case.rule_name or "")
    if not rule_name:
        rule_name = (target_rule_id or "Security Detection").replace("_", " ").title()
    confidence_pct = f"{readiness['confidence'] * 100:.0f}%"

    disposition_map = {
        "true_positive": "True Positive - Malicious Activity",
        "benign_positive": "Benign Positive - Suspicious But Expected",
        "false_positive": "False Positive - Incorrect Logic / Normal Activity",
        "other": "Other",
        "undetermined": "Undetermined / Inconclusive",
    }
    clean_disp_key = disposition.strip().lower().replace(" ", "_").replace("-", "_")
    formatted_disposition = disposition_map.get(clean_disp_key, disposition)

    evidence_summary = inv_state.get("evidence_summary") or {}
    supports = int((evidence_summary.get("by_finding") or {}).get("supports") or 0)
    refutes = int((evidence_summary.get("by_finding") or {}).get("refutes") or 0)
    neutral = int((evidence_summary.get("by_finding") or {}).get("neutral") or 0)
    incident_summary = (case.analysis_summary or inv_state.get("current_hypothesis") or "the security event").strip()
    # Case summaries often contain the full notable field dump. Keep the
    # operator-facing closure note focused on the incident name while the
    # complete notable and evidence remain available in the case ledger.
    incident_summary = re.split(r"\s*\|\s*(?:Status|Time):", incident_summary, maxsplit=1)[0].strip()
    incident_summary = incident_summary[:180].rstrip(" .,;:")
    compact_hypothesis = (inv_state.get("current_hypothesis") or incident_summary).strip()
    compact_hypothesis = re.split(r"\s*\|\s*(?:Status|Time):", compact_hypothesis, maxsplit=1)[0].strip()
    compact_hypothesis = compact_hypothesis[:180].rstrip(" .,;:")
    analyst_context = (analyst_notes or "").strip()
    generated_note = NL.join([
        f"Investigation of {incident_summary} used {len(evidence_rows)} durable evidence items across the rule-specific follow-up checks ({supports} supporting, {refutes} refuting, and {neutral} neutral/no-result findings).",
        f"The evidence-driven conclusion was {formatted_disposition.lower()} for {rule_name}; closure readiness was {'verified' if readiness['is_ready'] else 'not met'} with {confidence_pct} confidence.",
        analyst_context if analyst_context else f"Key conclusion: {compact_hypothesis or 'the investigation was resolved through the collected evidence'}.",
    ])

    closure_status = "closed" if readiness["is_ready"] else "submitted"
    closure_note = ClosureNote(
        case_id=case_id,
        rule_id=target_rule_id or "",
        analyst_notes=analyst_notes,
        generated_note=generated_note,
        status=closure_status,
        created_at=now,
        submitted_at=now,
    )
    db.add(closure_note)
    db.commit()

    return {
        "success": True,
        "blocked": False,
        "case_id": case_id,
        "rule_name": rule_name,
        "disposition": formatted_disposition,
        "closure_status": closure_status,
        "readiness": readiness,
        "generated_note": generated_note,
    }
