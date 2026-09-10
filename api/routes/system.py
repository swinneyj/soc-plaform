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
