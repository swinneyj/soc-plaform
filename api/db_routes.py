"""
Database and AI analysis endpoints for SOC Platform API.
"""

from fastapi import APIRouter, HTTPException, Query, Depends
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.orm import Session
from datetime import datetime

import sys
import os

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from db.models import TriageResult, SplunkEvent, AnalysisResult, get_db
from services.ollama_service import get_ollama_client, check_ollama_health

router = APIRouter(prefix="/api/db", tags=["Database"])

# Pydantic models for API
class TriageResultResponse(BaseModel):
    case_id: str
    rule_name: str
    verdict: str
    confidence_score: float
    analysis_summary: str
    remediation_steps: str
    triaged_at: datetime
    
    class Config:
        from_attributes = True

class AnalysisResultResponse(BaseModel):
    id: int
    case_id: str
    model_name: str
    query: str
    analysis: str
    confidence: float
    created_at: datetime
    
    class Config:
        from_attributes = True

class AnalyzeRequest(BaseModel):
    case_id: str = Query(..., description="Case ID to analyze")
    model: Optional[str] = Query("llama2", description="Ollama model to use")
    context: Optional[str] = Query("", description="Additional context")

# Database endpoints
@router.get("/triage", response_model=List[TriageResultResponse])
def get_triage_results(
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Get triaged cases from database."""
    results = db.query(TriageResult).limit(limit).offset(offset).all()
    return results

@router.get("/triage/{case_id}", response_model=TriageResultResponse)
def get_triage_case(case_id: str, db: Session = Depends(get_db)):
    """Get a specific triage case."""
    result = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
    if not result:
        raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
    return result

@router.get("/triage/verdict/{verdict}")
def get_cases_by_verdict(
    verdict: str,
    db: Session = Depends(get_db),
    limit: int = Query(100, ge=1, le=1000)
):
    """Get cases filtered by verdict (benign, suspicious, malicious)."""
    results = db.query(TriageResult).filter(
        TriageResult.verdict == verdict
    ).limit(limit).all()
    return results

@router.get("/stats")
def get_database_stats(db: Session = Depends(get_db)):
    """Get database statistics."""
    triage_count = db.query(TriageResult).count()
    splunk_count = db.query(SplunkEvent).count()
    analysis_count = db.query(AnalysisResult).count()
    
    # Verdict breakdown
    verdicts = db.query(
        TriageResult.verdict,
        func.count(TriageResult.verdict).label('count')
    ).group_by(TriageResult.verdict).all()
    
    return {
        "triage_cases": triage_count,
        "splunk_events": splunk_count,
        "analyses": analysis_count,
        "verdict_breakdown": {v[0]: v[1] for v in verdicts}
    }

# AI Analysis endpoints
@router.get("/ollama/health")
def check_ollama():
    """Check Ollama service status."""
    health = check_ollama_health()
    if not health["available"]:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama not available at {health['url']}. Start Ollama with: ollama serve"
        )
    return health

@router.post("/analyze")
def analyze_case(req: AnalyzeRequest, db: Session = Depends(get_db)):
    """
    Analyze a case using Ollama LLM.
    Queries triage results and generates AI analysis.
    """
    # Get the case
    case = db.query(TriageResult).filter(
        TriageResult.case_id == req.case_id
    ).first()
    
    if not case:
        raise HTTPException(status_code=404, detail=f"Case {req.case_id} not found")
    
    # Check Ollama
    client = get_ollama_client()
    if not client.available:
        raise HTTPException(
            status_code=503,
            detail="Ollama service not available"
        )
    
    # Build analysis prompt
    event_data = f"""
Case ID: {case.case_id}
Rule: {case.rule_name}
Initial Verdict: {case.verdict} (Confidence: {case.confidence_score})
Summary: {case.analysis_summary}
Remediation: {case.remediation_steps}
"""
    
    # Call Ollama
    result = client.analyze_security_event(event_data, req.context)
    
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result["error"])
    
    # Store analysis
    analysis = AnalysisResult(
        case_id=req.case_id,
        model_name=req.model,
        query=event_data,
        analysis=result["response"],
        confidence=0.5  # Placeholder; could parse from response
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    
    return {
        "case_id": req.case_id,
        "model": req.model,
        "analysis": result["response"],
        "stored_id": analysis.id
    }

@router.get("/analyses", response_model=List[AnalysisResultResponse])
def get_analyses(
    db: Session = Depends(get_db),
    case_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500)
):
    """Get previous analyses."""
    query = db.query(AnalysisResult)
    if case_id:
        query = query.filter(AnalysisResult.case_id == case_id)
    return query.order_by(AnalysisResult.created_at.desc()).limit(limit).all()

# Import needed for stats endpoint
from sqlalchemy import func
