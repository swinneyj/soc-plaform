"""System and runtime health endpoints."""

import datetime
import json

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["System"])
def health():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


@router.get("/api/health", tags=["System"])
def api_health():
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}


@router.get("/api/", tags=["System"])
def api_root():
    return {
        "name": "SOC Platform API",
        "version": "1.0.0",
        "description": "REST API for SOC Orchestration Platform with Local AI",
        "docs": "/docs",
    }


@router.get("/api/db/ollama/health", tags=["Database"])
def ollama_health():
    try:
        from services.ollama_service import check_ollama_health
        return check_ollama_health()
    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "models": [],
            "url": "http://host.docker.internal:11434",
        }


@router.get("/api/db/stats", tags=["Database"])
def db_stats():
    try:
        from sqlalchemy import func
        from db.models import AnalysisResult, SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()
        try:
            triage_count = db.query(TriageResult).count()
            splunk_count = db.query(SplunkEvent).count()
            analysis_count = db.query(AnalysisResult).count()
            verdict_rows = db.query(
                TriageResult.verdict, func.count(TriageResult.verdict)
            ).group_by(TriageResult.verdict).all()
            pasted_events = db.query(SplunkEvent).filter(
                SplunkEvent.sourcetype == "splunk:notable:pasted"
            ).all()
            historical = 0
            for event in pasted_events:
                try:
                    payload = json.loads(event.raw or "{}")
                except Exception:
                    payload = {}
                historical += int(bool(payload.get("historical")))
            return {
                "triage_cases": triage_count,
                "splunk_events": splunk_count,
                "analyses": analysis_count,
                "verdict_breakdown": {row[0]: row[1] for row in verdict_rows},
                "pasted_notables_total": len(pasted_events),
                "pasted_notables_open": len(pasted_events) - historical,
                "pasted_notables_historical": historical,
            }
        finally:
            db.close()
    except Exception as exc:
        return {"triage_cases": 0, "error": str(exc)}


@router.get("/api/db/operations", tags=["Database"])
def operations_dashboard():
    """Return operational case, closure, evidence, and tool-run metrics."""
    try:
        from datetime import datetime, timedelta
        from db.models import ClosureNote, InvestigationState, SessionLocal, SupportiveQueryResult, TriageResult, ToolRun
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            cases = db.query(TriageResult).all()
            states = {row.case_id: row for row in db.query(InvestigationState).all()}
            closed_states = {"closed", "closure_ready", "ready_for_closure", "resolved"}
            open_cases = [case for case in cases if (states.get(case.case_id).loop_status.lower() if states.get(case.case_id) and states.get(case.case_id).loop_status else "open") not in closed_states]

            def list_len(value):
                try:
                    parsed = json.loads(value or "[]")
                    return len(parsed) if isinstance(parsed, list) else 0
                except Exception:
                    return 0

            unresolved = sum(list_len(states[c.case_id].unresolved_questions) for c in open_cases if c.case_id in states)
            blockers = sum(list_len(states[c.case_id].closure_blockers) for c in open_cases if c.case_id in states)
            aging_24h = sum(1 for case in open_cases if case.triaged_at and now - case.triaged_at >= timedelta(hours=24))
            aging_7d = sum(1 for case in open_cases if case.triaged_at and now - case.triaged_at >= timedelta(days=7))
            notes = db.query(ClosureNote).filter(ClosureNote.submitted_at.isnot(None)).all()
            closure_hours = [(n.submitted_at - n.created_at).total_seconds() / 3600 for n in notes if n.created_at and n.submitted_at and n.submitted_at >= n.created_at]
            runs = db.query(ToolRun).all()
            failed = [run for run in runs if str(run.status).lower().replace("jobstatus.", "") == "failed"]
            return {
                "total_cases": len(cases), "open_cases": len(open_cases),
                "unresolved_questions": unresolved, "closure_blockers": blockers,
                "aging_24h": aging_24h, "aging_7d": aging_7d,
                "average_closure_hours": round(sum(closure_hours) / len(closure_hours), 1) if closure_hours else None,
                "closure_count": len(closure_hours), "tool_runs": len(runs),
                "tool_failures": len(failed), "evidence_results": db.query(SupportiveQueryResult).count(),
                "verdict_breakdown": {v or "undetermined": sum(1 for c in cases if (c.verdict or "undetermined") == v) for v in sorted({c.verdict or "undetermined" for c in cases})},
            }
        finally:
            db.close()
    except Exception as exc:
        return {"error": str(exc), "total_cases": 0, "open_cases": 0, "tool_runs": 0, "tool_failures": 0}
