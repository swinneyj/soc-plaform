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
import re
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


class PastedNotableRequest(BaseModel):
    raw_text: str = Field(..., description="Pasted notable text from Splunk Incident Review")
    redaction_enabled: bool = Field(
        True,
        description="Whether to apply tokenizer-style redaction to the pasted notable",
    )

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


NOTABLE_FIELD_ALIASES = [
    ("Additional FieldsValue", "additional_fields_value"),
    ("Additional Fields Value", "additional_fields_value"),
    ("Coorelation Search", "correlation_search"),
    ("Correlation Search", "correlation_search"),
    ("Security Domain", "security_domain"),
    ("Destination", "destination"),
    ("Destination Business Unit", "destination_business_unit"),
    ("Destination Category", "destination_category"),
    ("Destination DNS", "destination_dns"),
    ("Destination Expected", "destination_expected"),
    ("Destination IP Address", "destination_ip"),
    ("Destination NT Hostname", "destination_nt_hostname"),
    ("Destination PCI Domain", "destination_pci_domain"),
    ("Destination Port", "destination_port"),
    ("Disposition", "disposition"),
    ("Username", "username"),
    ("File Name", "file_name"),
    ("Risk Score", "risk_score"),
    ("Severity", "severity"),
    ("Urgency", "urgency"),
    ("Status", "status"),
    ("Actions", "actions"),
    ("Action", "action"),
    ("Owner", "owner"),
    ("Title", "title"),
    ("Type", "type"),
    ("Time", "time"),
    ("Host", "host"),
    ("Source IP Address", "source_ip"),
    ("Source Port", "source_port"),
    ("User Email", "user_email"),
    ("User First Name", "user_first_name"),
    ("User Last Name", "user_last_name"),
    ("User Identity", "user_identity"),
    ("User Category", "user_category"),
    ("User", "user"),
    ("Value", "value"),
]

NOTABLE_FIELD_LABELS = {
    "title": "Title",
    "correlation_search": "Correlation Search",
    "type": "Type",
    "time": "Time",
    "disposition": "Disposition",
    "urgency": "Urgency",
    "status": "Status",
    "owner": "Owner",
    "host": "Host",
    "destination": "Destination",
    "destination_business_unit": "Destination Business Unit",
    "destination_category": "Destination Category",
    "destination_dns": "Destination DNS",
    "destination_expected": "Destination Expected",
    "destination_ip": "Destination IP Address",
    "destination_nt_hostname": "Destination NT Hostname",
    "destination_pci_domain": "Destination PCI Domain",
    "destination_port": "Destination Port",
    "user": "User",
    "username": "Username",
    "user_email": "User Email",
    "user_first_name": "User First Name",
    "user_last_name": "User Last Name",
    "user_identity": "User Identity",
    "user_category": "User Category",
    "source_ip": "Source IP Address",
    "source_port": "Source Port",
    "actions": "Actions",
    "action": "Action",
    "additional_fields_value": "Additional FieldsValue",
    "value": "Value",
    "file_name": "File Name",
    "risk_score": "Risk Score",
    "security_domain": "Security Domain",
    "severity": "Severity",
}


def parse_pasted_notable(raw_text: str) -> Dict[str, str]:
    """Extract common Splunk notable key/value pairs from pasted text."""
    matches = []
    normalized = raw_text.replace("\r\n", "\n")

    for alias, canonical in sorted(NOTABLE_FIELD_ALIASES, key=lambda item: len(item[0]), reverse=True):
        for match in re.finditer(re.escape(alias), normalized, flags=re.IGNORECASE):
            matches.append({
                "start": match.start(),
                "end": match.end(),
                "canonical": canonical,
            })

    matches.sort(key=lambda item: (item["start"], -(item["end"] - item["start"])))

    deduped = []
    current_end = -1
    for match in matches:
        if match["start"] < current_end:
            continue
        deduped.append(match)
        current_end = match["end"]

    parsed: Dict[str, str] = {}
    for index, match in enumerate(deduped):
        value_start = match["end"]
        value_end = deduped[index + 1]["start"] if index + 1 < len(deduped) else len(normalized)
        value = normalized[value_start:value_end].strip(" \t:\n")
        value = re.sub(r"\s+", " ", value).strip()
        if value and match["canonical"] not in parsed:
            parsed[match["canonical"]] = value

    return parsed


def parse_structured_notable(raw_text: str) -> Dict[str, str]:
    """Parse line-oriented notable text in the form `Label: value`."""
    alias_map = {alias.lower(): canonical for alias, canonical in NOTABLE_FIELD_ALIASES}
    labels = sorted(alias_map.keys(), key=len, reverse=True)
    pattern = re.compile(rf"^\s*({'|'.join(re.escape(label) for label in labels)})\s*:\s*(.*)$", re.IGNORECASE)

    parsed: Dict[str, str] = {}
    for line in raw_text.replace("\r\n", "\n").split("\n"):
        match = pattern.match(line)
        if not match:
            continue

        alias = match.group(1).lower()
        value = re.sub(r"\s+", " ", match.group(2)).strip()
        canonical = alias_map.get(alias)
        if canonical and value and canonical not in parsed:
            parsed[canonical] = value

    return parsed


def render_notable_fields(parsed_fields: Dict[str, str]) -> str:
    ordered_keys = [label[1] for label in NOTABLE_FIELD_ALIASES]
    seen = set()
    lines = []
    for key in ordered_keys:
        if key in seen or key not in parsed_fields:
            continue
        seen.add(key)
        label = NOTABLE_FIELD_LABELS.get(key, key.replace("_", " ").title())
        lines.append(f"{label}: {parsed_fields[key]}")

    if not lines:
        return ""

    return "\n".join(lines)


def parse_notable_timestamp(value: str):
    if not value:
        return datetime.datetime.utcnow()

    candidate = value.strip()
    try:
        return datetime.datetime.fromisoformat(candidate)
    except ValueError:
        try:
            if len(candidate) > 5 and candidate[-3] == ':':
                compact_offset = candidate[:-3] + candidate[-2:]
                return datetime.datetime.strptime(compact_offset, "%Y-%m-%dT%H:%M:%S.000%z")
        except ValueError:
            pass

    return datetime.datetime.utcnow()


def save_notable_artifacts(platform_root: str, sanitized_text: str, mapping: Dict[str, str], parsed_fields: Dict[str, str]) -> Dict[str, str]:
    active_dir = os.path.join(platform_root, "Data", "Active_Workspace")
    os.makedirs(active_dir, exist_ok=True)

    timestamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    text_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.txt")
    fields_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.fields.json")
    mapping_path = os.path.join(active_dir, f"Pasted_Notable_{timestamp}.map.json")

    with open(text_path, "w", encoding="utf-8") as text_file:
        text_file.write(sanitized_text)

    with open(fields_path, "w", encoding="utf-8") as fields_file:
        json.dump(parsed_fields, fields_file, indent=2)

    with open(mapping_path, "w", encoding="utf-8") as mapping_file:
        json.dump(mapping, mapping_file, indent=2)

    latest_text = os.path.join(active_dir, "Pasted_Notable_latest.txt")
    latest_fields = os.path.join(active_dir, "Pasted_Notable_latest.fields.json")
    latest_map = os.path.join(active_dir, "Pasted_Notable_latest.map.json")

    for source_path, dest_path in ((text_path, latest_text), (fields_path, latest_fields), (mapping_path, latest_map)):
        try:
            import shutil
            shutil.copy2(source_path, dest_path)
        except Exception:
            pass

    return {
        "sanitized_text_path": text_path,
        "fields_path": fields_path,
        "mapping_path": mapping_path,
    }


def serialize_recent_notable(event) -> Dict[str, Any]:
    payload = {}
    try:
        payload = json.loads(event.raw) if event.raw else {}
    except Exception:
        payload = {"sanitized_text": event.raw}

    fields = payload.get("fields", {})
    return {
        "id": event.id,
        "promoted_case_id": payload.get("promoted_case_id"),
        "promoted_at": payload.get("promoted_at"),
        "title": fields.get("title") or event.source,
        "correlation_search": fields.get("correlation_search"),
        "type": fields.get("type"),
        "time": fields.get("time") or (event.timestamp.isoformat() if event.timestamp else None),
        "disposition": fields.get("disposition"),
        "urgency": fields.get("urgency"),
        "status": fields.get("status"),
        "owner": fields.get("owner"),
        "host": fields.get("host") or event.host,
        "destination": fields.get("destination"),
        "user": fields.get("user"),
        "username": fields.get("username"),
        "actions": fields.get("actions") or fields.get("action"),
        "severity": fields.get("severity"),
        "fields": fields,
        "sanitized_text": payload.get("sanitized_text", ""),
        "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
    }


def derive_triage_verdict(disposition: str) -> str:
    normalized = (disposition or "").strip().lower()
    if "false positive" in normalized or "benign" in normalized:
        return "benign"
    if "true positive" in normalized or "malicious" in normalized:
        return "malicious"
    return "suspicious"


def derive_triage_confidence(disposition: str) -> float:
    normalized = (disposition or "").strip().lower()
    if not normalized:
        return 0.5
    if "false positive" in normalized or "true positive" in normalized or "benign" in normalized:
        return 0.8
    return 0.6

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
    verdict: str = Query("", description="Filter by verdict"),
    delete_case_id: Optional[str] = Query(None, description="If provided, delete this case before listing"),
    delete_analysis: bool = Query(False, description="Also delete analysis results for this case when deleting"),
):
    """Get triaged cases from database (optionally deleting one first)."""
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import or_
        from db.models import SessionLocal, TriageResult

        db = SessionLocal()

        # Optional delete step
        if delete_case_id:
            try:
                delete_triage_case(case_id=delete_case_id, delete_analysis=delete_analysis)
            except HTTPException:
                # Surface delete errors via empty list; UI will already show alert
                pass

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


@app.post("/api/db/triage/{case_id}/delete", tags=["Database"])
def delete_triage_case(case_id: str, delete_analysis: bool = Query(False, description="Also delete analysis results for this case")):
    """Delete a triage case from the database.

    Intended mainly for removing test/development cases; this does not
    automatically delete any related analysis results.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, AnalysisResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Triage case {case_id} not found")

        db.delete(case)

        if delete_analysis:
            db.query(AnalysisResult).filter(AnalysisResult.case_id == case_id).delete()

        db.commit()

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.get("/api/db/triage/{case_id}/delete", tags=["Database"])
def delete_triage_case_get(case_id: str, delete_analysis: bool = Query(False, description="Also delete analysis results for this case")):
    """GET wrapper for delete_triage_case for environments that disallow POST."""
    return delete_triage_case(case_id=case_id, delete_analysis=delete_analysis)


@app.get("/api/db/triage/delete", tags=["Database"])
def delete_triage_case_query(
    case_id: str = Query(..., description="Case ID to delete"),
    delete_analysis: bool = Query(False, description="Also delete analysis results for this case"),
):
    """Delete a triage case using query parameters instead of a path parameter."""
    return delete_triage_case(case_id=case_id, delete_analysis=delete_analysis)


@app.post("/api/db/notables/paste", tags=["Database"])
def paste_notable(request: PastedNotableRequest):
    """Parse, sanitize, and store a pasted Splunk notable in the database."""
    raw_text = request.raw_text.strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="No notable text was provided")

    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent
        from text_sanitizer_pipeline.text_sanitizer_pipeline import sanitize_logs_with_tokens, sanitize_pii_phi

        parsed_fields = parse_structured_notable(raw_text)
        if not parsed_fields:
            parsed_fields = parse_pasted_notable(raw_text)
        structured_text = render_notable_fields(parsed_fields) or raw_text
        if request.redaction_enabled:
            sanitized_text, mapping = sanitize_logs_with_tokens(structured_text)
            sanitized_text = sanitize_pii_phi(sanitized_text)
            sanitized_fields = parse_structured_notable(sanitized_text)
            # preserve original time if we parsed it before masking
            if parsed_fields.get("time"):
                sanitized_fields["time"] = parsed_fields["time"]
        else:
            # no masking: keep parsed fields and structured text as-is
            sanitized_text = structured_text
            mapping = {}
            sanitized_fields = parsed_fields.copy()

        artifact_paths = save_notable_artifacts(get_platform_root(), sanitized_text, mapping, sanitized_fields)

        payload = {
            "record_type": "splunk_notable_paste",
            "fields": sanitized_fields,
            "sanitized_text": sanitized_text,
            "saved_at": datetime.datetime.utcnow().isoformat(),
            "artifact_paths": artifact_paths,
        }

        source = sanitized_fields.get("correlation_search") or sanitized_fields.get("title") or "Pasted Splunk notable"
        host = sanitized_fields.get("host") or sanitized_fields.get("destination") or "unknown"
        timestamp = parse_notable_timestamp(parsed_fields.get("time", ""))

        db = SessionLocal()
        event = SplunkEvent(
            sourcetype="splunk:notable:pasted",
            source=source,
            host=host,
            raw=json.dumps(payload),
            timestamp=timestamp,
        )
        db.add(event)
        db.commit()
        db.refresh(event)

        return {
            "success": True,
            "event_id": event.id,
            "parsed_fields": sanitized_fields,
            "artifact_paths": artifact_paths,
            "mapping_entries": len(mapping),
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


@app.get("/api/db/notables", tags=["Database"])
def list_recent_notables(
    limit: int = Query(20, ge=1, le=200),
    delete_event_id: Optional[int] = Query(None, description="If provided, delete this pasted notable before listing"),
):
    """List recently pasted sanitized Splunk notables (optionally deleting one first)."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()

        # Optional delete step
        if delete_event_id is not None:
            try:
                delete_pasted_notable(delete_event_id)
            except HTTPException:
                # Ignore delete errors here; UI will already show alert
                pass
        rows = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).limit(limit).all()

        return [serialize_recent_notable(row) for row in rows]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.delete("/api/db/notables/{event_id}", tags=["Database"])
def delete_pasted_notable(event_id: int):
    """Delete a pasted Splunk notable from the database.

    This removes the stored sanitized text and metadata for the pasted notable
    but does not delete any triage cases that may have been created from it.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted",
        ).first()

        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")

        db.delete(event)
        db.commit()

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.post("/api/db/notables/{event_id}/delete", tags=["Database"])
def delete_pasted_notable_post(event_id: int):
    """Compatibility endpoint to delete a pasted notable via POST.

    Some environments or proxies may not allow DELETE from the browser UI,
    so the frontend can call this POST variant instead. Logic is delegated
    to the main delete_pasted_notable handler above.
    """
    return delete_pasted_notable(event_id)


@app.get("/api/db/notables/{event_id}/delete", tags=["Database"])
def delete_pasted_notable_get(event_id: int):
    """GET wrapper for delete_pasted_notable for environments that disallow POST/DELETE."""
    return delete_pasted_notable(event_id)


@app.post("/api/db/notables/{event_id}/promote", tags=["Database"])
def promote_notable_to_triage(event_id: int):
    """Promote a pasted notable into the triage_results table."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult

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
        if existing_case_id:
            existing_case = db.query(TriageResult).filter(TriageResult.case_id == existing_case_id).first()
            if existing_case:
                return {
                    "success": True,
                    "already_promoted": True,
                    "case_id": existing_case.case_id,
                    "verdict": existing_case.verdict,
                }

        case_id = existing_case_id or f"NOTABLE-{event.id}"
        if db.query(TriageResult).filter(TriageResult.case_id == case_id).first():
            raise HTTPException(status_code=409, detail=f"Case ID {case_id} already exists")

        title = fields.get("title") or event.source or f"Pasted notable {event.id}"
        correlation_search = fields.get("correlation_search") or title
        disposition = fields.get("disposition", "")
        verdict = derive_triage_verdict(disposition)
        confidence = derive_triage_confidence(disposition)
        notable_time = fields.get("time") or (event.timestamp.isoformat() if event.timestamp else None)

        summary_parts = [title]
        if disposition:
            summary_parts.append(f"Disposition: {disposition}")
        if fields.get("status"):
            summary_parts.append(f"Status: {fields['status']}")
        if notable_time:
            summary_parts.append(f"Time: {notable_time}")
        if fields.get("host"):
            summary_parts.append(f"Host: {fields['host']}")
        if fields.get("destination"):
            summary_parts.append(f"Destination: {fields['destination']}")
        if fields.get("user") or fields.get("username"):
            summary_parts.append(f"User: {fields.get('user') or fields.get('username')}")

        remediation_steps = "Review the sanitized notable evidence, validate disposition, and gather any supporting host/user activity before closure."

        triage_case = TriageResult(
            case_id=case_id,
            rule_name=correlation_search,
            rule_id=None,
            verdict=verdict,
            confidence_score=confidence,
            analysis_summary=" | ".join(summary_parts),
            remediation_steps=remediation_steps,
            triaged_at=event.timestamp or datetime.datetime.utcnow(),
        )
        db.add(triage_case)

        payload["promoted_case_id"] = case_id
        payload["promoted_at"] = datetime.datetime.utcnow().isoformat()
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
