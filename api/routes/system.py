"""System and runtime health endpoints."""

import datetime
import json

from fastapi import APIRouter, Depends
from api.auth import require_role


def _utcnow() -> datetime.datetime:
    """Naive UTC now (platform convention) without the deprecated
    datetime.utcnow()."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


router = APIRouter()


@router.get("/health", tags=["System"])
def health():
    return {"status": "healthy", "timestamp": _utcnow().isoformat()}


@router.get("/api/health", tags=["System"])
def api_health():
    """OPS-01: liveness + database probe (SELECT 1 via SessionLocal).

    200 {"status": "healthy", "db": "connected"} when reachable;
    503 {"status": "unhealthy", "db": "disconnected"} when not.
    Never leaks driver errors (500 scrubber would also catch, but be explicit).

    Uses SessionLocal (not a fresh engine from db.target.resolve) so the
    pytest suite stays hermetic: conftest repoints SessionLocal at
    in-memory SQLite, and this probe follows it. In production SessionLocal
    is bound to Postgres, so this still probes the real database.
    """
    from fastapi.responses import JSONResponse
    try:
        from sqlalchemy import text as _text
        from db.models import SessionLocal as _SessionLocal
        db = _SessionLocal()
        try:
            db.execute(_text("SELECT 1"))
        finally:
            try:
                db.close()
            except Exception:
                pass
        return {"status": "healthy", "db": "connected", "timestamp": _utcnow().isoformat()}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "db": "disconnected", "timestamp": _utcnow().isoformat()},
        )


@router.get("/api/", tags=["System"], dependencies=[Depends(require_role("analyst"))])
def api_root():
    return {
        "name": "SOC Platform API",
        "version": "1.0.0",
        "description": "REST API for SOC Orchestration Platform with Local AI",
        "docs": "/docs",
    }


@router.get("/api/db/ollama/health", tags=["Database"], dependencies=[Depends(require_role("admin"))])
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


@router.get("/api/db/stats", tags=["Database"], dependencies=[Depends(require_role("admin"))])
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


@router.get("/api/db/operations", tags=["Database"], dependencies=[Depends(require_role("admin"))])
def operations_dashboard():
    """Return operational case, closure, evidence, and tool-run metrics."""
    try:
        from datetime import datetime, timedelta, timezone
        from db.models import ClosureNote, InvestigationState, SessionLocal, SupportiveQueryResult, TriageResult, ToolRun
        db = SessionLocal()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
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
