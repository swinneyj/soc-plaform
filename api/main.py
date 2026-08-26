"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from contextlib import asynccontextmanager
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create any missing tables on application startup."""
    from db.models import Base, engine
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(
    title="SOC Platform API",
    lifespan=lifespan,
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


class SupportiveQueryPayload(BaseModel):
    """Payload for creating/updating supportive SPL queries.

    This is intentionally minimal so analysts can tune queries on the fly
    without touching the underlying correlation rule definition.
    """

    rule_id: str = Field(..., description="Logical correlation rule identifier")
    title: str = Field(..., description="Short name for this supportive query")
    description: Optional[str] = Field("", description="What this query is used for")
    spl_query: str = Field(..., description="SPL to run in Splunk or another system")


class SupportiveQueryUpdatePayload(BaseModel):
    """Partial update payload for supportive SPL queries."""

    rule_id: Optional[str] = Field(None, description="Logical correlation rule identifier")
    title: Optional[str] = Field(None, description="Short name for this supportive query")
    description: Optional[str] = Field(None, description="What this query is used for")
    spl_query: Optional[str] = Field(None, description="SPL to run in Splunk or another system")


class PlaceholderAliasPayload(BaseModel):
    """Payload for creating/updating placeholder aliases.

    Aliases let analysts define logical names (e.g., "host", "dest",
    "user") that map to one or more notable fields without touching
    code. These are consumed by the frontend when rendering supportive
    queries with $placeholder$ tokens.
    """

    alias: str = Field(..., description="Logical placeholder name (e.g., host, dest, user)")
    fields: List[str] = Field(..., description="Candidate field names to resolve values from")
    description: Optional[str] = Field("", description="Human-readable description of this alias")


class PlaceholderAliasUpdatePayload(BaseModel):
    """Partial update payload for placeholder aliases."""

    alias: Optional[str] = Field(None, description="Logical placeholder name (e.g., host, dest, user)")
    fields: Optional[List[str]] = Field(None, description="Candidate field names to resolve values from")
    description: Optional[str] = Field(None, description="Human-readable description of this alias")


class PastedNotableRequest(BaseModel):
    raw_text: str = Field(..., description="Pasted notable text from Splunk Incident Review")
    redaction_enabled: bool = Field(
        True,
        description="Whether to apply tokenizer-style redaction to the pasted notable",
    )
    historical: bool = Field(
        False,
        description="Whether this pasted notable represents a closed/historical case",
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
    ("Process", "process"),
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
    "process": "Process",
    "risk_score": "Risk Score",
    "security_domain": "Security Domain",
    "severity": "Severity",
}


def parse_pasted_notable(raw_text: str) -> Dict[str, str]:
    """Extract common Splunk notable key/value pairs from pasted text."""
    matches = []
    normalized = raw_text.replace("\r\n", "\n")
    # Prefer label occurrences at the beginning of lines (how Splunk renders
    # the Additional Fields grid), to avoid matching words inside long
    # sentences such as correlation rule names.
    for alias, canonical in sorted(NOTABLE_FIELD_ALIASES, key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(rf"(^|\n)[ \t]*({re.escape(alias)})\b", flags=re.IGNORECASE)
        for match in pattern.finditer(normalized):
            # Capture just the alias span, not the leading newline/whitespace.
            alias_start = match.start(2)
            alias_end = match.end(2)
            matches.append({
                "start": alias_start,
                "end": alias_end,
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


def normalize_notable_fields(fields: Dict[str, str]) -> Dict[str, str]:
    """Apply small, conservative fix-ups to parsed notable fields.

    Current behaviors:
    - If Destination NT Hostname is present and Destination looks merged or
      empty, prefer Destination NT Hostname as the Destination value. This
      compensates for copy/paste glitches where the Destination row is
      concatenated with the next label (e.g., "Destination NDC56-10Risk Score").
    """

    # Normalize destination from destination_nt_hostname when appropriate.
    dest_nt = (fields.get("destination_nt_hostname") or "").strip()
    if dest_nt:
        dest = (fields.get("destination") or "").strip()
        dest_nt_norm = dest_nt.lower()
        dest_norm = dest.lower()
        # If destination is missing OR clearly contains the NT hostname with
        # extra suffix characters (case-insensitive), prefer the cleaner
        # hostname value.
        if not dest or (dest_nt_norm in dest_norm and len(dest) > len(dest_nt)):
            fields["destination"] = dest_nt

    return fields


def split_pasted_notables(raw_text: str) -> List[str]:
    """Split a bulk paste that may contain multiple notables into segments.

    Heuristic: treat each line starting with a primary heading ("Title" or
    "Correlation Search") as the beginning of a new notable block. This
    matches the common Splunk Incident Review copy/paste format where each
    notable starts with its own Title/Correlation Search section.
    """
    if not raw_text or not raw_text.strip():
        return []

    normalized = raw_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    # Primary heuristic: each card contains a standalone "Notable" line near the top.
    # Use a case-sensitive match so we only pick up the top-of-card
    # "Notable" heading, not the lower-case "notable" line under
    # Event Details.
    notable_pattern = re.compile(r"^\s*Notable\s*$")
    boundaries: List[int] = [
        index for index, line in enumerate(lines) if notable_pattern.match(line)
    ]

    # Fallback heuristic: use Title/Correlation Search labels when the Notable pattern
    # doesn't give us multiple segments.
    if len(boundaries) <= 1:
        heading_pattern = re.compile(r"^\s*(Title|Correlation Search)\b", re.IGNORECASE)
        boundaries = [
            index for index, line in enumerate(lines) if heading_pattern.match(line)
        ]

    # If we still didn't find multiple headings, treat the whole paste as a single notable.
    if len(boundaries) <= 1:
        single = normalized.strip()
        return [single] if single else []

    segments: List[str] = []
    for i, start in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(lines)
        segment_lines = lines[start:end]
        segment = "\n".join(segment_lines).strip()
        if segment:
            segments.append(segment)

    # Fallback: if something went wrong, at least return the whole text once.
    if not segments:
        single = normalized.strip()
        return [single] if single else []

    return segments


def extract_notable_history(raw_text: str) -> str:
    """Extract the History/closure-notes section from a single notable block.

    We look for a line that is exactly "History" (case-insensitive) and then
    consume subsequent lines until we hit a known boundary marker such as
    "View all review activity", "Drill-down Search", "Adaptive Responses",
    or "Next Steps". This mirrors how Splunk ES renders review history in the
    Incident Review UI.
    """
    if not raw_text:
        return ""

    normalized = raw_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    start_index = None
    for index, line in enumerate(lines):
        if line.strip().lower() == "history":
            start_index = index
            break

    if start_index is None or start_index + 1 >= len(lines):
        return ""

    end_markers = (
        "view all review activity",
        "drill-down search",
        "adaptive responses",
        "next steps",
    )

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        lower = lines[index].strip().lower()
        if any(lower.startswith(marker) for marker in end_markers):
            end_index = index
            break

    history_lines = lines[start_index + 1:end_index]
    history_text = "\n".join(history_lines).strip()
    return history_text


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


def build_historical_dedup_key(fields: Dict[str, str], sanitized_text: str) -> Optional[str]:
    """Build a stable key for deduplicating closed/historical pasted notables.

    We rely on a combination of correlation search/title, notable time, host,
    and the sanitized text body. This is intentionally conservative and only
    used for historical (closed) notables, so a match strongly suggests the
    same incident has already been stored.
    """
    if not sanitized_text:
        return None

    title = (fields.get("title") or "").strip()
    corr = (fields.get("correlation_search") or "").strip()
    time_val = (fields.get("time") or "").strip()
    host_val = (fields.get("host") or fields.get("destination") or "").strip()

    anchor = corr or title
    if not anchor:
        return None

    # Normalize whitespace in sanitized_text to reduce trivial diffs
    normalized_text = re.sub(r"\s+", " ", sanitized_text).strip()
    return "|".join([anchor, time_val, host_val, normalized_text]) or None


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
        "historical": payload.get("historical", False),
        "history": payload.get("history"),
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
        from db.models import SessionLocal, TriageResult, AnalysisResult

        db = SessionLocal()

        # Optional delete step using the same session
        if delete_case_id:
            case = db.query(TriageResult).filter(TriageResult.case_id == delete_case_id).first()
            if case:
                db.delete(case)
                if delete_analysis:
                    db.query(AnalysisResult).filter(AnalysisResult.case_id == delete_case_id).delete()
                db.commit()

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
                "rule_id": r.rule_id,
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


@app.get("/api/db/triage/{case_id}", tags=["Database"])
def get_triage_case(case_id: str):
    """Get a specific triage case by case ID."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult

        db = SessionLocal()
        result = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not result:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        return {
            "case_id": result.case_id,
            "rule_name": result.rule_name,
            "rule_id": result.rule_id,
            "verdict": result.verdict,
            "confidence_score": result.confidence_score,
            "analysis_summary": result.analysis_summary,
            "remediation_steps": result.remediation_steps,
            "triaged_at": result.triaged_at.isoformat()
        }
    finally:
        try:
            if db is not None:
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
        segments = split_pasted_notables(raw_text)
        if not segments:
            raise HTTPException(status_code=400, detail="Unable to detect any notable segments in the pasted text")

        db = SessionLocal()

        events_info = []
        total_mapping_entries = 0

        # For closed/historical notables, avoid creating duplicate rows when the
        # same Incident Review export is pasted multiple times. We build a map
        # of existing historical events keyed by a composite of
        # correlation_search/title, time, host, and sanitized text.
        existing_historical: Dict[str, Any] = {}
        if request.historical:
            existing_events = db.query(SplunkEvent).filter(
                SplunkEvent.sourcetype == "splunk:notable:pasted"
            ).order_by(SplunkEvent.ingested_at.desc()).all()

            for event in existing_events:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    continue

                if not payload.get("historical"):
                    continue

                fields = payload.get("fields", {}) or {}
                sanitized_text_existing = payload.get("sanitized_text", "")
                key = build_historical_dedup_key(fields, sanitized_text_existing)
                if key and key not in existing_historical:
                    existing_historical[key] = {
                        "event": event,
                        "payload": payload,
                        "fields": fields,
                        "artifact_paths": payload.get("artifact_paths") or {},
                    }

        for index, segment_text in enumerate(segments):
            parsed_fields = parse_structured_notable(segment_text)
            if not parsed_fields:
                parsed_fields = parse_pasted_notable(segment_text)

            # Apply small normalization tweaks (e.g., Destination from
            # Destination NT Hostname) before rendering/sanitizing.
            parsed_fields = normalize_notable_fields(parsed_fields)

            base_structured_text = render_notable_fields(parsed_fields) or segment_text
            history_text = extract_notable_history(segment_text)
            if history_text:
                structured_text = f"{base_structured_text}\n\nHistory\n{history_text}"
            else:
                structured_text = base_structured_text

            if request.redaction_enabled:
                sanitized_text, mapping = sanitize_logs_with_tokens(structured_text)
                sanitized_text = sanitize_pii_phi(sanitized_text)
                sanitized_fields = parse_structured_notable(sanitized_text)
                # preserve original time if we parsed it before masking
                if parsed_fields.get("time"):
                    sanitized_fields["time"] = parsed_fields["time"]
                sanitized_fields = normalize_notable_fields(sanitized_fields)
            else:
                # no masking: keep parsed fields and structured text as-is
                sanitized_text = structured_text
                mapping = {}
                sanitized_fields = normalize_notable_fields(parsed_fields.copy())

            sanitized_history = extract_notable_history(sanitized_text)

            # Deduplicate closed/historical notables to avoid duplicate rows
            # when the same incident is pasted multiple times.
            artifact_paths = None
            event = None

            if request.historical:
                key = build_historical_dedup_key(sanitized_fields, sanitized_text)
                existing = existing_historical.get(key) if key else None
                if existing:
                    event = existing["event"]
                    artifact_paths = existing.get("artifact_paths") or {}
                else:
                    artifact_paths = save_notable_artifacts(get_platform_root(), sanitized_text, mapping, sanitized_fields)
                    payload = {
                        "record_type": "splunk_notable_paste",
                        "fields": sanitized_fields,
                        "sanitized_text": sanitized_text,
                        "history": sanitized_history,
                        "saved_at": datetime.datetime.utcnow().isoformat(),
                        "artifact_paths": artifact_paths,
                        "historical": request.historical,
                        "segment_index": index,
                        "segment_count": len(segments),
                    }

                    source = sanitized_fields.get("correlation_search") or sanitized_fields.get("title") or "Pasted Splunk notable"
                    host = sanitized_fields.get("host") or sanitized_fields.get("destination") or "unknown"
                    timestamp = parse_notable_timestamp(parsed_fields.get("time", ""))

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

                    # Cache this new historical event for any additional
                    # segments that may match within the same paste.
                    if key:
                        existing_historical[key] = {
                            "event": event,
                            "payload": payload,
                            "fields": sanitized_fields,
                            "artifact_paths": artifact_paths,
                        }
            else:
                artifact_paths = save_notable_artifacts(get_platform_root(), sanitized_text, mapping, sanitized_fields)
                payload = {
                    "record_type": "splunk_notable_paste",
                    "fields": sanitized_fields,
                    "sanitized_text": sanitized_text,
                    "history": sanitized_history,
                    "saved_at": datetime.datetime.utcnow().isoformat(),
                    "artifact_paths": artifact_paths,
                    "historical": request.historical,
                    "segment_index": index,
                    "segment_count": len(segments),
                }

                source = sanitized_fields.get("correlation_search") or sanitized_fields.get("title") or "Pasted Splunk notable"
                host = sanitized_fields.get("host") or sanitized_fields.get("destination") or "unknown"
                timestamp = parse_notable_timestamp(parsed_fields.get("time", ""))

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

            events_info.append({
                "event_id": event.id if event else None,
                "parsed_fields": sanitized_fields,
                "artifact_paths": artifact_paths or {},
                "mapping_entries": len(mapping),
                "deduplicated": bool(request.historical and key and existing),
            })
            total_mapping_entries += len(mapping)

        first_event = events_info[0]

        return {
            "success": True,
            "segment_count": len(segments),
            "events": events_info,
            # Backwards-compatible single-event fields (use first segment)
            "event_id": first_event["event_id"],
            "parsed_fields": first_event["parsed_fields"],
            "artifact_paths": first_event["artifact_paths"],
            "mapping_entries": total_mapping_entries,
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

        # Optional delete step using the same session
        if delete_event_id is not None:
            event = db.query(SplunkEvent).filter(
                SplunkEvent.id == delete_event_id,
                SplunkEvent.sourcetype == "splunk:notable:pasted",
            ).first()
            if event:
                db.delete(event)
                db.commit()
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
        disposition = fields.get("disposition", "")
        verdict = derive_triage_verdict(disposition)
        confidence = derive_triage_confidence(disposition)
        notable_time = fields.get("time") or (event.timestamp.isoformat() if event.timestamp else None)

        # Try to resolve a stable rule_id from the ES correlation rules
        # table so future cases for the same rule consistently reuse the
        # same supportive queries and templates.
        resolved_rule_id = None
        anchor = (correlation_search or "").strip().lower()
        if anchor:
            rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()

            # 1) Prefer exact rule_name match
            for r in rules:
                name = (r.rule_name or "").strip().lower()
                if name and name == anchor:
                    resolved_rule_id = r.rule_id
                    break

            # 2) Fallback to relaxed contains-based match
            if not resolved_rule_id:
                for r in rules:
                    name = (r.rule_name or "").strip().lower()
                    if not name:
                        continue
                    if anchor in name or name in anchor:
                        resolved_rule_id = r.rule_id
                        break

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
            rule_id=resolved_rule_id,
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


@app.get("/api/db/triage/{case_id}/notable", tags=["Database"])
def get_triage_source_notable(case_id: str):
    """Return the source pasted notable details for a promoted triage case."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

        for event in events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            if payload.get("promoted_case_id") == case_id:
                fields = payload.get("fields", {})
                return {
                    "event_id": event.id,
                    "historical": payload.get("historical", False),
                    "fields": fields,
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "saved_at": payload.get("saved_at") or (
                        event.ingested_at.isoformat() if event.ingested_at else None
                    ),
                }

        raise HTTPException(status_code=404, detail=f"Source pasted notable for case {case_id} not found")
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
    """Analyze a case using Ollama LLM.

    Enhanced flow:
    - Uses the triage case details as the primary anchor.
    - Pulls the source pasted notable (sanitized text + history) for this case.
    - Finds closed/historical pasted notables with matching correlation search/title
      to serve as baselines.
    - Includes any stored supportive query results for this case.
    - Optionally appends analyst-provided context.

    All of this is fused into a single composite prompt sent to the local
    Ollama model.
    """
    try:
        case_id = request.case_id
        model = request.model
        context = request.context

        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQueryResult
        from services.ollama_service import get_ollama_client

        db = SessionLocal()

        # Anchor on the triage case
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        # Load all pasted-notable events once for both source and baselines
        pasted_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

        # Identify the source pasted notable for this case (if any)
        source_notable_payload = None
        for event in pasted_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            if payload.get("promoted_case_id") == case_id:
                source_notable_payload = {
                    "event_id": event.id,
                    "fields": payload.get("fields", {}),
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "historical": payload.get("historical", False),
                    "saved_at": payload.get("saved_at") or (
                        event.ingested_at.isoformat() if event.ingested_at else None
                    ),
                }
                break

        # Build closed/historical baselines for the same rule/title
        correlation_anchor = (case.rule_name or "").strip()
        historical_baselines = []
        if correlation_anchor:
            for event in pasted_events:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    continue

                if not payload.get("historical"):
                    continue

                fields = payload.get("fields", {})
                title = fields.get("title") or event.source or ""
                corr = fields.get("correlation_search") or ""

                if correlation_anchor not in (title, corr):
                    continue

                historical_baselines.append({
                    "event_id": event.id,
                    "fields": fields,
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "saved_at": payload.get("saved_at") or (
                        event.ingested_at.isoformat() if event.ingested_at else None
                    ),
                })

        # Load supportive query results for this case
        supportive_rows = db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id
        ).order_by(SupportiveQueryResult.created_at.desc()).all()

        supportive_results = []
        for r in supportive_rows:
            try:
                raw = json.loads(r.raw_result) if r.raw_result else None
            except Exception:
                raw = r.raw_result

            supportive_results.append({
                "id": r.id,
                "rule_id": r.rule_id,
                "query_title": r.query_title,
                "source_system": r.source_system,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "raw_result": raw,
            })

        db.close()

        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")

        # Build composite prompt
        prompt_parts = []
        prompt_parts.append(
            "You are a SOC analyst reviewing a security case. "
            "Use the current case, closed/historical baselines, and supportive query "
            "results below to recommend a disposition, key investigative steps, and "
            "concise closure notes."
        )

        # Current triage case
        prompt_parts.append("\n\n=== CURRENT CASE ===")
        prompt_parts.append(f"Case ID: {case.case_id}")
        prompt_parts.append(f"Rule Name / Title: {case.rule_name}")
        prompt_parts.append(f"Verdict: {case.verdict}")
        prompt_parts.append(f"Analysis Summary: {case.analysis_summary}")
        if case.remediation_steps:
            prompt_parts.append(f"Remediation Steps: {case.remediation_steps}")
        prompt_parts.append(f"Triaged At: {case.triaged_at.isoformat()}")

        # Source pasted notable (open case)
        if source_notable_payload:
            prompt_parts.append("\n\n=== SOURCE PASTED NOTABLE (OPEN) ===")
            prompt_parts.append(f"Event ID: {source_notable_payload['event_id']}")
            fields = source_notable_payload.get("fields", {})
            if fields:
                prompt_parts.append("Fields:")
                for key, value in fields.items():
                    prompt_parts.append(f"- {key}: {value}")
            history = source_notable_payload.get("history")
            if history:
                prompt_parts.append("\nHistory / closure notes:")
                prompt_parts.append(history)
            sanitized_text = source_notable_payload.get("sanitized_text")
            if sanitized_text:
                prompt_parts.append("\nSanitized text:")
                prompt_parts.append(sanitized_text)

        # Closed/historical baselines
        if historical_baselines:
            prompt_parts.append("\n\n=== CLOSED BASELINE NOTABLES (HISTORICAL) ===")
            for idx, baseline in enumerate(historical_baselines, start=1):
                prompt_parts.append(
                    f"\n[Baseline {idx}] Event ID: {baseline['event_id']} "
                    f"(saved at {baseline['saved_at']})"
                )
                fields = baseline.get("fields") or {}
                if fields:
                    prompt_parts.append("Fields:")
                    for key, value in fields.items():
                        prompt_parts.append(f"- {key}: {value}")
                history = baseline.get("history")
                if history:
                    prompt_parts.append("History / closure notes:")
                    prompt_parts.append(history)
                sanitized_text = baseline.get("sanitized_text")
                if sanitized_text:
                    prompt_parts.append("Sanitized text:")
                    prompt_parts.append(sanitized_text)

        # Supportive query results
        if supportive_results:
            prompt_parts.append("\n\n=== SUPPORTIVE QUERY RESULTS ===")
            for idx, result_row in enumerate(supportive_results, start=1):
                prompt_parts.append(
                    f"\n[Supportive Result {idx}] "
                    f"Title: {result_row['query_title']} "
                    f"(source: {result_row['source_system']}, rule_id: {result_row['rule_id']})"
                )
                raw = result_row.get("raw_result")
                if raw is not None:
                    prompt_parts.append("Raw result:")
                    if isinstance(raw, (dict, list)):
                        prompt_parts.append(json.dumps(raw, indent=2))
                    else:
                        prompt_parts.append(str(raw))

        # Optional analyst-provided context
        if context:
            prompt_parts.append("\n\n=== ANALYST-PROVIDED CONTEXT ===")
            prompt_parts.append(context)

        composite_prompt = "\n".join(prompt_parts)

        result = client.generate(composite_prompt, model=model)

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        return {
            "case_id": case_id,
            "model": model,
            "analysis": result["response"],
            "baseline_notables_count": len(historical_baselines),
            "supportive_results_count": len(supportive_results),
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


@app.get("/api/db/supportive-queries", tags=["Rules"])
def list_supportive_queries(rule_id: Optional[str] = Query(default=None, description="Filter by logical rule_id")):
    """List supportive SPL queries.

    When a rule_id is provided, only queries for that rule are returned.
    Otherwise, all supportive queries are listed. This API backs the
    analyst-facing editor so supportive queries can be tuned on the fly
    without touching JSON seed files.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        query = db.query(SupportiveQuery)
        if rule_id:
            query = query.filter(SupportiveQuery.rule_id == rule_id)
        rows = query.order_by(SupportiveQuery.rule_id.asc(), SupportiveQuery.title.asc()).all()
        db.close()

        return [
            {
                "id": r.id,
                "rule_id": r.rule_id,
                "title": r.title,
                "description": r.description,
                "spl_query": r.spl_query,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/db/placeholder-aliases", tags=["Rules"])
def list_placeholder_aliases():
    """List all defined placeholder aliases.

    Response shape matches what the frontend expects:
    [{"id", "alias", "fields", "description"}, ...]
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        rows = db.query(PlaceholderAlias).order_by(PlaceholderAlias.alias.asc()).all()
        db.close()

        aliases: List[Dict[str, Any]] = []
        for row in rows:
            try:
                fields = json.loads(row.fields) if row.fields else []
            except Exception:
                fields = []

            aliases.append({
                "id": row.id,
                "alias": (row.alias or "").strip(),
                "fields": fields,
                "description": row.description or "",
            })

        return aliases
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/db/placeholder-aliases", tags=["Rules"])
def create_placeholder_alias(payload: PlaceholderAliasPayload):
    """Create a new placeholder alias.

    Alias names are normalized to lowercase and must be unique.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        alias_normalized = payload.alias.strip().lower()
        if not alias_normalized:
            db.close()
            raise HTTPException(status_code=400, detail="Alias name cannot be empty")

        # Enforce uniqueness at the application level for clearer errors.
        existing = db.query(PlaceholderAlias).filter(
            func.lower(PlaceholderAlias.alias) == alias_normalized
        ).first()
        if existing:
            db.close()
            raise HTTPException(status_code=400, detail=f"Alias '{alias_normalized}' already exists")

        cleaned_fields = [f.strip() for f in (payload.fields or []) if f and f.strip()]
        record = PlaceholderAlias(
            alias=alias_normalized,
            fields=json.dumps(cleaned_fields),
            description=(payload.description or "").strip(),
        )

        db.add(record)
        db.commit()
        db.refresh(record)
        db.close()

        return {
            "id": record.id,
            "alias": record.alias,
            "fields": cleaned_fields,
            "description": record.description or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/db/placeholder-aliases/{alias_id}", tags=["Rules"])
def update_placeholder_alias(alias_id: int, payload: PlaceholderAliasUpdatePayload):
    """Update an existing placeholder alias.

    Supports partial updates for alias, fields, and description.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        record = db.query(PlaceholderAlias).filter(PlaceholderAlias.id == alias_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Placeholder alias {alias_id} not found")

        if payload.alias is not None:
            new_alias = payload.alias.strip().lower()
            if not new_alias:
                db.close()
                raise HTTPException(status_code=400, detail="Alias name cannot be empty")

            existing = db.query(PlaceholderAlias).filter(
                func.lower(PlaceholderAlias.alias) == new_alias,
                PlaceholderAlias.id != alias_id,
            ).first()
            if existing:
                db.close()
                raise HTTPException(status_code=400, detail=f"Alias '{new_alias}' already exists")
            record.alias = new_alias

        if payload.fields is not None:
            cleaned_fields = [f.strip() for f in payload.fields if f and f.strip()]
            record.fields = json.dumps(cleaned_fields)

        if payload.description is not None:
            record.description = payload.description.strip()

        db.commit()
        db.refresh(record)
        db.close()

        try:
            fields = json.loads(record.fields) if record.fields else []
        except Exception:
            fields = []

        return {
            "id": record.id,
            "alias": record.alias,
            "fields": fields,
            "description": record.description or "",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/db/placeholder-aliases/{alias_id}", tags=["Rules"])
def delete_placeholder_alias(alias_id: int):
    """Delete a placeholder alias definition."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, PlaceholderAlias

        db = SessionLocal()
        record = db.query(PlaceholderAlias).filter(PlaceholderAlias.id == alias_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Placeholder alias {alias_id} not found")

        db.delete(record)
        db.commit()
        db.close()

        return {"success": True, "deleted_id": alias_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/db/supportive-queries", tags=["Rules"])
def create_supportive_query(payload: SupportiveQueryPayload):
    """Create a new supportive SPL query for a correlation rule.

    IDs are assigned explicitly based on the current max(id) to avoid
    depending on a potentially misaligned Postgres sequence, mirroring
    the import logic used by the ES rules importer.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        try:
            max_id = db.query(func.max(SupportiveQuery.id)).scalar() or 0
        except Exception:
            max_id = 0
        next_id = int(max_id) + 1

        record = SupportiveQuery(
            id=next_id,
            rule_id=payload.rule_id.strip(),
            title=payload.title.strip(),
            description=(payload.description or "").strip(),
            spl_query=payload.spl_query.strip(),
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        db.close()

        return {
            "id": record.id,
            "rule_id": record.rule_id,
            "title": record.title,
            "description": record.description,
            "spl_query": record.spl_query,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/db/supportive-queries/{query_id}", tags=["Rules"])
def update_supportive_query(query_id: int, payload: SupportiveQueryUpdatePayload):
    """Update an existing supportive SPL query.

    Supports partial updates; any field omitted from the payload is left
    unchanged. Rule IDs can be adjusted if needed when re-grouping
    queries under a different logical rule.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        record = db.query(SupportiveQuery).filter(SupportiveQuery.id == query_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Supportive query {query_id} not found")

        if payload.rule_id is not None:
            record.rule_id = payload.rule_id.strip()
        if payload.title is not None:
            record.title = payload.title.strip()
        if payload.description is not None:
            record.description = payload.description.strip()
        if payload.spl_query is not None:
            record.spl_query = payload.spl_query.strip()

        db.commit()
        db.refresh(record)
        db.close()

        return {
            "id": record.id,
            "rule_id": record.rule_id,
            "title": record.title,
            "description": record.description,
            "spl_query": record.spl_query,
            "created_at": record.created_at.isoformat() if record.created_at else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/db/supportive-queries/{query_id}", tags=["Rules"])
def delete_supportive_query(query_id: int):
    """Delete a supportive SPL query.

    This does not touch stored supportive_query_results; those remain as
    historical evidence even if the underlying query definition is
    retired.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQuery

        db = SessionLocal()
        record = db.query(SupportiveQuery).filter(SupportiveQuery.id == query_id).first()
        if not record:
            db.close()
            raise HTTPException(status_code=404, detail=f"Supportive query {query_id} not found")

        db.delete(record)
        db.commit()
        db.close()

        return {"success": True, "deleted_id": query_id}
    except HTTPException:
        raise
    except Exception as e:
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

        by_rule: Dict[str, List[Dict[str, Any]]] = {}
        for sq in all_supportive:
            by_rule.setdefault(sq.rule_id, []).append({
                "id": sq.id,
                "title": sq.title,
                "description": sq.description,
                "spl_query": sq.spl_query,
            })

        def supportive_for_rule(rule_obj) -> List[Dict[str, Any]]:
            """Return supportive queries for a given rule.

            In addition to queries explicitly keyed to this rule_id, we
            support lightweight family sharing for LotL-style rules: any
            rule whose ID starts with ``lotl_`` automatically inherits the
            supportive queries defined under the canonical
            ``lotl_outbound_connection`` family, unless duplicates exist.

            This lets future LotL notables reuse the same investigation
            SPL without duplicating query definitions in the database.
            """

            rule_id = (rule_obj.rule_id or "").strip()
            base = list(by_rule.get(rule_id, []))

            # LotL family sharing: treat any "lotl_*" rule as part of the
            # same investigative family and reuse the canonical queries.
            canonical_lotl_id = "lotl_outbound_connection"
            if rule_id.startswith("lotl_") and rule_id != canonical_lotl_id:
                extras = by_rule.get(canonical_lotl_id, []) or []
                existing_titles = {q["title"] for q in base}
                for q in extras:
                    if q["title"] not in existing_titles:
                        base.append(q)

            return base

        return [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name,
                "description": r.description,
                "category": r.category,
                "severity": r.severity,
                "drilldown_fields": json.loads(r.drilldown_fields) if r.drilldown_fields else [],
                "required_closure_fields": json.loads(r.required_closure_fields) if r.required_closure_fields else [],
                "supportive_queries": supportive_for_rule(r),
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
