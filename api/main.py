"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import os
import sys
import json
import uuid
import subprocess
import datetime
from pathlib import Path
from enum import Enum

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

from core_lib.utils import get_platform_root, get_reports_dir, get_archive_dir, get_logs_dir

app = FastAPI(
    title="SOC Platform API",
    description="REST API for SOC Orchestration Platform tools and workflows with local AI analysis",
    version="1.0.0",
)

# In-memory job tracking (in production, use Redis)
jobs: Dict[str, Dict[str, Any]] = {}

# Pydantic Models
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

class ToolRequest(BaseModel):
    tool_name: str = Field(..., description="Name of the tool to execute")
    arguments: Optional[Dict[str, str]] = Field(default={}, description="Tool CLI arguments")
    silent: bool = Field(default=False, description="Suppress stdout output")

class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    tool_name: str
    created_at: str
    completed_at: Optional[str] = None
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None

class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None

class AnalyzeRequest(BaseModel):
    case_id: str
    model: str = "llama2"
    context: str = ""

# Helper Functions
def load_registry() -> List[Dict]:
    """Load tool registry from JSON."""
    registry_path = os.path.join(get_platform_root(), 'Commander_Registry.json')
    if not os.path.exists(registry_path):
        return []
    with open(registry_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _fix_tool_path(tool_path: str) -> str:
    """Resolve registry paths from either absolute or repo-relative form."""
    platform_root = get_platform_root()
    if not os.path.isabs(tool_path):
        relative_path = os.path.join(platform_root, tool_path)
        if os.path.exists(relative_path):
            return relative_path
    normalized = tool_path.replace("\\", "/")
    if "Tools/" in normalized:
        parts = normalized.split("Tools/")
        if len(parts) > 1:
            rel_part = parts[-1]
            relative_path = os.path.join(platform_root, "Tools", rel_part.replace("/", os.sep))
            if os.path.exists(relative_path):
                return relative_path
    return tool_path

def execute_tool_sync(tool_path: str, args: Dict[str, str], silent: bool = False) -> Dict[str, Any]:
    """Execute a tool synchronously and return stdout/stderr/exit_code."""
    cmd = [sys.executable, tool_path]
    for key, val in args.items():
        if val:
            cmd.extend([f'--{key}', str(val)])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=get_platform_root()
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Tool execution timed out after 300 seconds",
            "exit_code": -1
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1
        }

def execute_tool_async(job_id: str, tool_path: str, args: Dict[str, str], silent: bool = False):
    """Background task to execute tool asynchronously."""
    jobs[job_id]["status"] = JobStatus.RUNNING
    result = execute_tool_sync(tool_path, args, silent)
    jobs[job_id].update({
        "status": JobStatus.COMPLETED if result["exit_code"] == 0 else JobStatus.FAILED,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "completed_at": datetime.datetime.utcnow().isoformat()
    })

# Routes
@app.get("/health", tags=["System"])
def health():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

@app.get("/api/health", tags=["System"])
def api_health():
    """Compatibility health endpoint for UI callers that expect an /api prefix."""
    return {"status": "healthy", "timestamp": datetime.datetime.utcnow().isoformat()}

@app.get("/api/", tags=["System"])
def api_root():
    """API root with metadata."""
    return {
        "name": "SOC Platform API",
        "version": "1.0.0",
        "description": "REST API for SOC Orchestration Platform with Local AI",
        "docs": "/docs"
    }

@app.get("/api/tools", response_model=List[ToolInfo], tags=["Tools"])
def list_tools():
    """List all available tools with metadata."""
    registry = load_registry()
    return [
        ToolInfo(
            name=t["name"],
            file_name=t.get("file_name", ""),
            category=t.get("category", "Uncategorized"),
            description=t.get("description", "No description"),
            path=_fix_tool_path(t["path"]),
            arguments=t.get("arguments", [])
        )
        for t in registry
    ]

@app.get("/api/tools/{tool_name}", response_model=ToolInfo, tags=["Tools"])
def get_tool(tool_name: str):
    """Get metadata for a specific tool."""
    registry = load_registry()
    tool = next((t for t in registry if t["name"].lower() == tool_name.lower()), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return ToolInfo(
        name=tool["name"],
        file_name=tool.get("file_name", ""),
        category=tool.get("category", "Uncategorized"),
        description=tool.get("description", "No description"),
        path=_fix_tool_path(tool["path"]),
        arguments=tool.get("arguments", [])
    )

@app.post("/api/execute", response_model=JobResponse, tags=["Execution"])
def execute_tool(request: ToolRequest, background_tasks: BackgroundTasks):
    """Execute a tool asynchronously and return a job ID."""
    registry = load_registry()
    tool = next((t for t in registry if t["name"].lower() == request.tool_name.lower()), None)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{request.tool_name}' not found")
    tool_path = _fix_tool_path(tool["path"])
    if not os.path.exists(tool_path):
        raise HTTPException(status_code=400, detail=f"Tool script not found: {tool_path}")
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "job_id": job_id,
        "status": JobStatus.PENDING,
        "tool_name": request.tool_name,
        "created_at": datetime.datetime.utcnow().isoformat(),
        "completed_at": None,
        "stdout": None,
        "stderr": None,
        "exit_code": None
    }
    background_tasks.add_task(
        execute_tool_async,
        job_id,
        tool_path,
        request.arguments or {},
        request.silent
    )
    return JobResponse(**jobs[job_id])

@app.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Execution"])
def get_job_status(job_id: str):
    """Retrieve job status and results."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return JobResponse(**jobs[job_id])

@app.get("/api/jobs", response_model=List[JobResponse], tags=["Execution"])
def list_jobs(status: Optional[JobStatus] = Query(None, description="Filter by job status")):
    """List all jobs, optionally filtered by status."""
    job_list = list(jobs.values())
    if status:
        job_list = [j for j in job_list if j["status"] == status]
    return [JobResponse(**j) for j in job_list]

@app.get("/api/reports", tags=["Data"])
def list_reports():
    """List all generated reports."""
    reports_dir = get_reports_dir()
    reports = []
    if os.path.exists(reports_dir):
        for fname in os.listdir(reports_dir):
            fpath = os.path.join(reports_dir, fname)
            if os.path.isfile(fpath):
                reports.append({
                    "filename": fname,
                    "size": os.path.getsize(fpath),
                    "modified": datetime.datetime.fromtimestamp(os.path.getmtime(fpath)).isoformat()
                })
    return sorted(reports, key=lambda x: x["modified"], reverse=True)

@app.get("/api/reports/{report_name}", tags=["Data"])
def download_report(report_name: str):
    """Download a specific report file."""
    report_path = os.path.join(get_reports_dir(), report_name)
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail=f"Report '{report_name}' not found")
    return FileResponse(
        path=report_path,
        filename=report_name,
        media_type="text/plain"
    )

@app.get("/api/registry", tags=["System"])
def get_registry():
    """Get the full tool registry as JSON."""
    return load_registry()

@app.post("/api/registry/reload", tags=["System"])
def reload_registry():
    """Force a registry rebuild (runs tool_indexer)."""
    indexer_path = os.path.join(get_platform_root(), 'Tools', 'tool_indexer', 'tool_indexer.py')
    if not os.path.exists(indexer_path):
        raise HTTPException(status_code=400, detail="Tool indexer not found")
    try:
        result = subprocess.run(
            [sys.executable, indexer_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=get_platform_root()
        )
        return {
            "status": "success" if result.returncode == 0 else "failed",
            "message": result.stdout + result.stderr
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Database & AI Analysis Endpoints
@app.get("/api/db/ollama/health", tags=["Database"])
def ollama_health():
    """Check Ollama service health and list available models."""
    try:
        sys.path.insert(0, get_platform_root())
        from services.ollama_service import check_ollama_health
        return check_ollama_health()
    except Exception as e:
        return {"available": False, "error": str(e), "models": [], "url": "http://host.docker.internal:11434"}

@app.get("/api/db/stats", tags=["Database"])
def db_stats():
    """Get database statistics."""
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func
        from db.models import AnalysisResult, SessionLocal, SplunkEvent, TriageResult
        db = SessionLocal()
        triage_count = db.query(TriageResult).count()
        splunk_count = db.query(SplunkEvent).count()
        analysis_count = db.query(AnalysisResult).count()
        verdict_rows = db.query(
            TriageResult.verdict,
            func.count(TriageResult.verdict)
        ).group_by(TriageResult.verdict).all()
        db.close()
        return {
            "triage_cases": triage_count,
            "splunk_events": splunk_count,
            "analyses": analysis_count,
            "verdict_breakdown": {row[0]: row[1] for row in verdict_rows}
        }
    except Exception as e:
        return {"triage_cases": 0, "error": str(e)}

@app.get("/api/db/triage", tags=["Database"])
def get_triage(
    limit: int = Query(50, ge=1, le=1000),
    search: str = Query("", description="Search case ID, rule name, or summary"),
    verdict: str = Query("", description="Filter by verdict")
):
    """Get triaged cases from database."""
    try:
        sys.path.insert(0, get_platform_root())
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

        return [
            {
                "case_id": r.case_id,
                "rule_name": r.rule_name,
                "verdict": r.verdict,
                "confidence_score": r.confidence_score,
                "analysis_summary": r.analysis_summary,
                "remediation_steps": r.remediation_steps,
                "triaged_at": r.triaged_at.isoformat()
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

@app.post("/api/db/analyze", tags=["Database"])
def analyze_case(request: AnalyzeRequest):
    """Analyze a case using Ollama LLM."""
    try:
        case_id = request.case_id
        model = request.model
        context = request.context
        
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult
        from services.ollama_service import get_ollama_client
        
        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        db.close()
        
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
        
        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")
        
        event_data = f"Case ID: {case.case_id}\nRule: {case.rule_name}\nVerdict: {case.verdict}\nSummary: {case.analysis_summary}"
        result = client.generate(event_data, model=model)
        
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])
        
        return {
            "case_id": case_id,
            "model": model,
            "analysis": result["response"]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/db/supportive-results/{case_id}", tags=["Database"])
def get_supportive_results(case_id: str):
    """Get any stored supportive query results for a case.

    These are the enrichment outputs from running supportive SPL/SQL
    outside the platform and loading them into supportive_query_results.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQueryResult
        db = SessionLocal()
        rows = db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id
        ).order_by(SupportiveQueryResult.created_at.desc()).all()
        db.close()

        return [
            {
                "id": r.id,
                "case_id": r.case_id,
                "rule_id": r.rule_id,
                "query_title": r.query_title,
                "source_system": r.source_system,
                "raw_result": json.loads(r.raw_result) if r.raw_result else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as e:
        # For now, surface a basic error instead of an empty list so we can debug
        raise HTTPException(status_code=500, detail=str(e))

# Rules & Closure Notes Endpoints
@app.get("/api/db/rules", tags=["Rules"])
def list_rules():
    """List all available ES correlation rules, including any supportive queries."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, SupportiveQuery
        db = SessionLocal()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()

        # Preload supportive queries for all rules
        all_supportive = db.query(SupportiveQuery).all()
        db.close()

        by_rule = {}
        for sq in all_supportive:
            by_rule.setdefault(sq.rule_id, []).append({
                "id": sq.id,
                "title": sq.title,
                "description": sq.description,
                "spl_query": sq.spl_query
            })

        return [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "description": r.description,
                "category": r.category,
                "severity": r.severity,
                "drilldown_fields": json.loads(r.drilldown_fields) if r.drilldown_fields else [],
                "required_closure_fields": json.loads(r.required_closure_fields) if r.required_closure_fields else [],
                "supportive_queries": by_rule.get(r.rule_id, [])
            }
            for r in rules
        ]
    except Exception:
        return []

@app.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate a closure note for a case based on rule template."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, ClosureNote, TriageResult
        
        rule_id = request.get('rule_id')
        case_id = request.get('case_id')
        field_values = request.get('field_values', {})
        analyst_notes = request.get('analyst_notes', '')
        disposition = request.get('disposition', 'Undetermined')
        
        if not rule_id or not case_id:
            raise HTTPException(status_code=400, detail="rule_id and case_id required")
        
        db = SessionLocal()
        rule = db.query(ESCorrelationRule).filter(ESCorrelationRule.rule_id == rule_id).first()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        
        if not rule:
            db.close()
            raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found")
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")
        
        # Extract all data while session is active
        rule_name = rule.rule_name
        rule_template = rule.closure_template
        
        # Build closure note from template - add all replacement values
        all_values = dict(field_values)
        all_values['rule_name'] = rule_name
        all_values['case_id'] = case_id
        all_values['date'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        all_values['disposition'] = disposition
        
        try:
            generated_note = rule_template.format(**all_values)
        except KeyError as e:
            db.close()
            raise HTTPException(status_code=400, detail=f"Missing field in template: {str(e)}")
        
        # Map disposition to status
        status_map = {
            'True Positive': 'true_positive',
            'Benign Positive': 'benign_positive',
            'False Positive': 'false_positive',
            'Other': 'other',
            'Undetermined': 'undetermined'
        }
        closure_status = status_map.get(disposition, 'undetermined')
        
        # Save closure note
        closure_note = ClosureNote(
            case_id=case_id,
            rule_id=rule_id,
            analyst_notes=analyst_notes,
            generated_note=generated_note,
            status=closure_status
        )
        db.add(closure_note)
        db.commit()
        db.close()
        
        return {
            "success": True,
            "case_id": case_id,
            "rule_name": rule_name,
            "disposition": disposition,
            "generated_note": generated_note
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Global exception handler for unhandled errors."""
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)}
    )

# Serve web UI
web_dir = os.path.join(os.path.dirname(__file__), '..', 'web')
if os.path.exists(web_dir):
    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
