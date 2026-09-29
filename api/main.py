"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
import logging
import os
import sys
import json
import uuid
import subprocess
import datetime
import re
import io
import csv
import zipfile
import ast
from pathlib import Path

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

from core_lib.utils import get_platform_root, get_reports_dir

logger = logging.getLogger("soc.api")
# Router split: shared flow helpers live in api.flow_support and the
# promote / analyze / evidence / closure endpoints live in api.routes.*.
# The names re-exported here keep existing callers (including the test
# suite) working against api.main unchanged.
from api.flow_support import (  # noqa: F401
    TRIAGE_KEY_FIELD_PRIORITY,
    JobStatus,
    _utcnow,
    InvestigationEvidenceBatchPayload,
    InvestigationEvidenceEntryPayload,
    _build_question_driven_followup_queries,
    _evidence_entry_is_valid,
    _field_lookup,
    extract_triage_key_fields,
)
from api.routes.analyze import router as _analyze_router
from api.routes.closure import router as _closure_router
from api.routes.evidence import router as _evidence_router
from api.routes.evidence import save_case_evidence  # noqa: F401
from api.routes.notables import router as _notables_router
from api.routes.promote import derive_triage_confidence  # noqa: F401
from api.routes.promote import router as _promote_router
from api.routes.splunk import router as _splunk_router
from api.routes.splunk import _SEARCH_ONE_INFLIGHT  # noqa: F401
from api.routes.triage import router as _triage_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create any missing tables on application startup."""
    # Database connectivity is optional for the hosted shell and health
    # routes.  In Vercel, importing the models must not make startup fail
    # just because DATABASE_URL is absent or temporarily unreachable.
    try:
        from db.models import Base, engine
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        app.state.database_startup_error = str(exc)
    yield

# Interactive API docs are a recon convenience for local development but an
# attack-surface map in production, so they are disabled unless explicitly
# enabled (set ENABLE_DOCS=1 in .env for local development).
_enable_docs = os.environ.get("ENABLE_DOCS", "") == "1"

# ---------------------------------------------------------------------------
# API-key auth (Phase 5 groundwork: ready to activate).
#
# Setting API_KEY activates gating; leaving it unset keeps local development
# friction free (localhost-only exposure). Coverage (Phase 5):
#   - middleware below: EVERY mutating /api/* request (POST/PUT/PATCH/DELETE)
#     — this is the default, so future routes are gated the moment they exist;
#   - explicit `dependencies=[Depends(require_api_key)]` on the dangerous
#     routes below stays as defense in depth.
# Deliberately NOT gated (read-only / health / static UI): /api/health and
# other GETs, the mounted web UI. The frontend already stamps X-API-Key on
# every axios request (web/utils/auth.js reads window.SOC_CONFIG.apiKey or
# ?apiKey= for local testing), so activation is: put API_KEY in the BWS vault
# + .env, set SOC_CONFIG.apiKey in the deployed HTML, restart.
# ---------------------------------------------------------------------------
# The key itself lives in api.auth — one patch point for the middleware and
# the require_api_key dependency (re-exported here for route decorators).
from api import auth  # noqa: E402
from api.auth import require_api_key  # noqa: F401


app = FastAPI(
    title="SOC Platform API",
    lifespan=lifespan,
    description="REST API for SOC Orchestration Platform tools and workflows with local AI analysis",
    version="1.0.0",
    docs_url="/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _enable_docs else None,
)


@app.middleware("http")
async def _api_key_mutation_gate(request, call_next):
    """Reject unauthenticated mutating /api/* requests when API_KEY is set.

    Read-only GETs, /api/health (deploy smoke test), and the static UI stay
    open per the contract above. The key is accepted as the X-API-Key header
    (what web/utils/auth.js sends) or an ?api_key= query parameter.
    """
    if auth.mutation_gate_rejects(request) and not auth.request_has_api_key(request):
        return JSONResponse(
            {"detail": "Invalid or missing API key"}, status_code=401
        )
    return await call_next(request)

# The hosted branch preview can call a locally running API through a secure
# HTTPS tunnel. Keep the allowlist explicit; do not enable wildcard CORS for
# the SOC data and analysis endpoints.
cors_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ORIGINS", "").split(",")
    if origin.strip()
]
if cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=False,
        # The frontend uses exactly these methods and headers (see
        # web/modules/api.js); keep the allowlist tight instead of "*".
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Accept", "X-API-Key"],
    )

from api.routes.system import router as system_router
app.include_router(system_router)
app.include_router(_analyze_router)
app.include_router(_closure_router)
app.include_router(_evidence_router)
app.include_router(_notables_router)
app.include_router(_promote_router)
app.include_router(_splunk_router)
app.include_router(_triage_router)


# In-memory job tracking (in production, use Redis)
jobs: Dict[str, Dict[str, Any]] = {}
deleted_job_ids = set()
STALE_JOB_SECONDS = 600

# Pydantic Models
def _job_status_value(status: Any) -> str:
    """Normalize enum instances and legacy persisted enum strings."""
    if isinstance(status, JobStatus):
        return status.value
    value = str(status or "").strip()
    if value.startswith("JobStatus."):
        value = value.split(".", 1)[1].lower()
    return value.lower()

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
    arguments: Optional[Dict[str, Any]] = None
    artifacts: List[str] = Field(default_factory=list)

class ToolInfo(BaseModel):
    name: str
    file_name: str
    category: str
    description: str
    path: str
    arguments: Optional[List[Dict[str, Any]]] = None



class SupportiveQueryPayload(BaseModel):
    """Payload for creating/updating supportive SPL queries.

    This is intentionally minimal so analysts can tune queries on the fly
    without touching the underlying correlation rule definition.
    """

    rule_id: str = Field(..., description="Logical correlation rule identifier")
    title: str = Field(..., description="Short name for this supportive query")
    description: Optional[str] = Field("", description="What this query is used for")
    spl_query: str = Field(..., description="SPL to run in Splunk or another system")


class SupportivePlaybookDraftRequest(BaseModel):
    case_id: str = Field(..., description="Case used to ground the draft playbook")


class SupportiveResultsImportRequest(BaseModel):
    case_id: str = Field(..., description="Case used to ground the draft playbook")
    content: str = Field(..., min_length=1, max_length=2_000_000, description="Pasted or uploaded Splunk results")
    filename: Optional[str] = Field(None, description="Original filename, if uploaded")


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


def _rebuild_placeholder_aliases_file() -> None:
    """Persist current placeholder aliases into placeholder_aliases.json.

    This mirrors the DB state into a repo-backed JSON file so alias
    definitions can survive DB restores when sync_shared_logic_to_db.ps1
    re-imports shared logic.
    """
    try:
        from db.models import SessionLocal, PlaceholderAlias  # type: ignore

        db = SessionLocal()
        try:
            rows = (
                db.query(PlaceholderAlias)
                .order_by(PlaceholderAlias.alias.asc())
                .all()
            )
            aliases: List[Dict[str, Any]] = []
            for row in rows:
                try:
                    fields = json.loads(row.fields) if row.fields else []
                except Exception:
                    fields = []
                aliases.append(
                    {
                        "alias": (row.alias or "").strip(),
                        "fields": fields,
                        "description": row.description or "",
                    }
                )

            payload = {"aliases": aliases}
            output_path = os.path.join(get_platform_root(), "placeholder_aliases.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        finally:
            db.close()
    except Exception as e:
        logger.warning("[placeholder_aliases.json sync] Failed to rebuild file: %s", e)


def _extract_python_sections(code_snippet: str) -> List[Dict[str, Any]]:
    """Extract functions/classes from Python code using the AST.

    Returns a list of sections with stable IDs, line ranges, and
    previews that the frontend can use for per-function review.
    """
    try:
        tree = ast.parse(code_snippet)
    except SyntaxError:
        return []

    lines = code_snippet.splitlines()
    sections: List[Dict[str, Any]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            kind = "function"
            name = node.name
        elif isinstance(node, ast.AsyncFunctionDef):
            kind = "async function"
            name = node.name
        elif isinstance(node, ast.ClassDef):
            kind = "class"
            name = node.name
        else:
            continue

        start_line = getattr(node, "lineno", None) or 1
        end_line = getattr(node, "end_lineno", None) or start_line

        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        preview_lines = lines[start_idx:end_idx]
        preview = "\n".join(preview_lines).strip()
        if not preview:
            continue

        sections.append(
            {
                "id": f"{kind}:{name}:{start_line}",
                "name": name,
                "kind": kind,
                "start_line": start_line,
                "end_line": end_line,
                "preview": preview,
            }
        )

    sections.sort(key=lambda s: (s["start_line"], s["name"]))
    return sections


def _extract_code_sections(code_snippet: str, language: str) -> List[Dict[str, Any]]:
    """Extract code sections (functions/classes) for the given language.

    For Python this uses the AST for precise function/class ranges.
    For other languages it falls back to lightweight regex heuristics.
    """
    language = (language or "").lower().strip()
    if not code_snippet.strip():
        return []

    if language == "python":
        return _extract_python_sections(code_snippet)

    # For HTML/HTM, treat the content as a Vue/JS script and
    # focus on top-level methods inside the `methods:` block so
    # we don't overwhelm the UI with every inline if/for.
    if language in ("html", "htm"):
        lines = code_snippet.splitlines()
        sections: List[Dict[str, Any]] = []

        reserved_names = {"if", "for", "while", "switch", "try", "catch", "finally"}

        brace_depth = 0
        inside_methods = False

        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track brace depth roughly so we know when we've
            # left the methods block.
            brace_depth += line.count("{")
            brace_depth -= line.count("}")

            if "methods:" in stripped:
                inside_methods = True
                # Methods block will start at the next opening brace
                continue

            if inside_methods and brace_depth <= 0:
                inside_methods = False

            if not inside_methods:
                continue

            # Vue-style method definitions: loadTools() { ... }
            m = re.search(r"^\s*(async\s+)?([A-Za-z0-9_$]+)\s*\([^)]*\)\s*\{", line)
            if not m:
                continue

            name = m.group(2).strip()
            if not name or name in reserved_names:
                continue

            start_line = idx
            end_line = min(len(lines), idx + 60)
            body = "\n".join(lines[idx - 1:end_line]).strip()
            if not body:
                continue

            sections.append(
                {
                    "id": f"method:{name}:{start_line}",
                    "name": name,
                    "kind": "method",
                    "label": f"methods.{name}",
                    "start_line": start_line,
                    "end_line": end_line,
                    "preview": body,
                }
            )

        sections.sort(key=lambda s: (s["start_line"], s["name"]))
        return sections

    lines = code_snippet.splitlines()
    sections: List[Dict[str, Any]] = []

    def add_regex_sections(pattern: str, kind: str) -> None:
        compiled = re.compile(pattern)
        for idx, line in enumerate(lines, start=1):
            match = compiled.search(line)
            if not match:
                continue
            name = match.group(1).strip()
            start_line = idx
            # Capture a reasonable slice of the function/body below the definition.
            end_line = min(len(lines), idx + 40)
            preview_lines = lines[idx - 1:end_line]
            preview = "\n".join(preview_lines).strip()
            if not preview:
                continue
            sections.append(
                {
                    "id": f"{kind}:{name}:{start_line}",
                    "name": name,
                    "kind": kind,
                    "start_line": start_line,
                    "end_line": end_line,
                    "preview": preview,
                }
            )

    # JavaScript/TypeScript: extract named functions and simple
    # const-as-function patterns, but skip obvious control-flow
    # names to avoid noise.
    if language in ("javascript", "js", "ts"):
        reserved_names = {"if", "for", "while", "switch", "try", "catch", "finally"}
        # Named functions: function foo(...) {
        def add_js_sections(pattern: str, kind: str) -> None:
            compiled = re.compile(pattern)
            for idx, line in enumerate(lines, start=1):
                match = compiled.search(line)
                if not match:
                    continue
                name = match.group(1).strip()
                if not name or name in reserved_names:
                    continue
                start_line = idx
                end_line = min(len(lines), idx + 40)
                preview_lines = lines[idx - 1:end_line]
                preview = "\n".join(preview_lines).strip()
                if not preview:
                    continue
                sections.append(
                    {
                        "id": f"{kind}:{name}:{start_line}",
                        "name": name,
                        "kind": kind,
                        "start_line": start_line,
                        "end_line": end_line,
                        "preview": preview,
                    }
                )

        add_js_sections(r"\bfunction\s+([A-Za-z0-9_$]+)\s*\(", "function")
        # Simple const foo = (...) patterns
        add_js_sections(r"\bconst\s+([A-Za-z0-9_$]+)\s*=\s*\(", "function")
    elif language == "go":
        add_regex_sections(r"\bfunc\s+([A-Za-z0-9_]+)\s*\(", "function")
    elif language == "bash":
        add_regex_sections(r"\b([A-Za-z0-9_]+)\s*\(\)\s*\{", "function")
    elif language == "sql":
        add_regex_sections(r"\bCREATE\s+(?:FUNCTION|PROCEDURE)\s+([A-Za-z0-9_]+)", "procedure")

    sections.sort(key=lambda s: (s["start_line"], s["name"]))
    return sections


def _rebuild_supportive_rules_file() -> None:
    """Persist current supportive queries from DB into supportive_rules.json.

    This keeps the repo-backed supportive_rules.json in sync with DB edits
    made via the API/UI so that a later DB restore followed by
    sync_shared_logic_to_db.ps1 can automatically reapply supportive
    queries without manual JSON editing.
    """
    try:
        from db.models import SessionLocal, SupportiveQuery  # type: ignore

        db = SessionLocal()
        try:
            rows = (
                db.query(SupportiveQuery)
                .order_by(SupportiveQuery.rule_id.asc(), SupportiveQuery.title.asc())
                .all()
            )

            grouped: Dict[str, List[Dict[str, Any]]] = {}
            for row in rows:
                grouped.setdefault(row.rule_id, []).append(
                    {
                        "title": row.title,
                        "description": row.description or "",
                        "spl_query": row.spl_query,
                    }
                )

            payload = {
                "rules": [
                    {"rule_id": rule_id, "supportive_queries": queries}
                    for rule_id, queries in grouped.items()
                ]
            }

            output_path = os.path.join(get_platform_root(), "supportive_rules.json")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        finally:
            db.close()
    except Exception as e:  # best-effort only; never break API on failure
        logger.warning("[supportive_rules.json sync] Failed to rebuild file: %s", e)

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

def _tool_artifact_snapshot() -> set:
    """Return files that tool runs may create, using portable paths."""
    root = get_platform_root()
    candidates = [
        os.path.join(root, "Reports"),
        os.path.join(root, "Data", "Reports"),
        os.path.join(root, "Data", "Archive"),
        os.path.join(root, "Data", "Active_Workspace"),
        os.path.join(root, "Data", "Exports"),
    ]
    found = set()
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        for base, _, files in os.walk(folder):
            for name in files:
                path = os.path.join(base, name)
                try:
                    found.add(os.path.realpath(path))
                except OSError:
                    continue
    return found


def execute_tool_sync(tool_path: str, args: Dict[str, str], silent: bool = False) -> Dict[str, Any]:
    """Execute a tool synchronously and return stdout/stderr/exit_code."""
    cmd = [sys.executable, tool_path]
    boolean_flags = {
        "silent", "list", "use_cases", "replace_all_supportive",
    }
    for key, val in args.items():
        if key in boolean_flags:
            if val is True or str(val).strip().lower() in {"1", "true", "yes", "on"}:
                cmd.append(f"--{key.replace('_', '-')}")
        elif val:
            cmd.extend([f'--{key}', str(val)])
    before = _tool_artifact_snapshot()
    try:
        tool_env = os.environ.copy()
        # API jobs must never block waiting for a terminal-only prompt.
        tool_env["COMMANDER_BOOT"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=get_platform_root(),
            env=tool_env,
        )
        after = _tool_artifact_snapshot()
        artifacts = [os.path.relpath(p, get_platform_root()).replace(os.sep, "/") for p in sorted(after - before)]
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
            "artifacts": artifacts,
        }
    except subprocess.TimeoutExpired:
        return {
            "stdout": "",
            "stderr": "Tool execution timed out after 300 seconds",
            "exit_code": -1,
            "artifacts": [],
        }
    except Exception as e:
        return {
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "artifacts": [],
        }

def execute_tool_async(job_id: str, tool_path: str, args: Dict[str, str], silent: bool = False):
    """Background task to execute tool asynchronously."""
    if job_id in deleted_job_ids or job_id not in jobs:
        return
    jobs[job_id]["status"] = JobStatus.RUNNING.value
    result = execute_tool_sync(tool_path, args, silent)
    if job_id in deleted_job_ids or job_id not in jobs:
        return
    jobs[job_id].update({
        "status": JobStatus.COMPLETED.value if result["exit_code"] == 0 else JobStatus.FAILED.value,
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "artifacts": result.get("artifacts", []),
        "completed_at": _utcnow().isoformat()
    })
    _persist_tool_run(jobs[job_id])


def _persist_tool_run(job: Dict[str, Any]) -> None:
    """Best-effort persistence; tool execution must not fail on DB outages."""
    try:
        from db.models import SessionLocal, ToolRun
        db = SessionLocal()
        row = db.get(ToolRun, job["job_id"])
        if row is None:
            row = ToolRun(job_id=job["job_id"])
            db.add(row)
        row.tool_name = job.get("tool_name")
        row.arguments = json.dumps(job.get("arguments") or {})
        row.status = _job_status_value(job.get("status"))
        row.stdout = job.get("stdout")
        row.stderr = job.get("stderr")
        row.exit_code = job.get("exit_code")
        row.artifact_paths = json.dumps(job.get("artifacts") or [])
        row.completed_at = datetime.datetime.fromisoformat(job["completed_at"]) if job.get("completed_at") else None
        db.commit()
        db.close()
    except Exception as exc:
        logger.warning("[tool_runs] Persistence unavailable: %s", exc)


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

@app.post("/api/tools/regression", tags=["Tools"], dependencies=[Depends(require_api_key)])
def run_tool_catalog_regression():
    """Run safe offline regression checks for the registered tool catalog."""
    runner = os.path.join(get_platform_root(), "scripts", "run_tool_catalog_regression.py")
    if not os.path.exists(runner):
        raise HTTPException(status_code=404, detail="Catalog regression runner not found")
    result = subprocess.run(
        [sys.executable, runner, "--json"], cwd=get_platform_root(),
        capture_output=True, text=True, timeout=180,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=result.stdout + result.stderr)
    payload["exit_code"] = result.returncode
    return payload

@app.post("/api/execute", response_model=JobResponse, tags=["Execution"], dependencies=[Depends(require_api_key)])
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
    deleted_job_ids.discard(job_id)
    jobs[job_id] = {
        "job_id": job_id,
        "status": JobStatus.PENDING.value,
        "tool_name": request.tool_name,
        "created_at": _utcnow().isoformat(),
        "completed_at": None,
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "arguments": request.arguments or {},
        "artifacts": [],
    }
    _persist_tool_run(jobs[job_id])
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
        try:
            from db.models import SessionLocal, ToolRun
            db = SessionLocal()
            row = db.get(ToolRun, job_id)
            db.close()
            if row is not None:
                return JobResponse(
                    job_id=row.job_id,
                    status=_job_status_value(row.status),
                    tool_name=row.tool_name,
                    created_at=row.created_at.isoformat() if row.created_at else "",
                    completed_at=row.completed_at.isoformat() if row.completed_at else None,
                    stdout=row.stdout,
                    stderr=row.stderr,
                    exit_code=row.exit_code,
                    arguments=json.loads(row.arguments or "{}"),
                    artifacts=json.loads(row.artifact_paths or "[]"),
                )
        except Exception as exc:
            logger.warning("[tool_runs] Could not load persisted job: %s", exc)
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    job = jobs[job_id]
    job["status"] = _job_status_value(job.get("status"))
    return JobResponse(**job)

@app.get("/api/jobs", response_model=List[JobResponse], tags=["Execution"])
def list_jobs(status: Optional[JobStatus] = Query(None, description="Filter by job status")):
    """List all jobs, optionally filtered by status."""
    job_list = list(jobs.values())
    for job in job_list:
        job["status"] = _job_status_value(job.get("status"))
    try:
        from db.models import SessionLocal, ToolRun
        db = SessionLocal()
        persisted = db.query(ToolRun).order_by(ToolRun.created_at.desc()).limit(100).all()
        db.close()
        live_ids = {j["job_id"] for j in job_list}
        for row in persisted:
            if row.job_id in live_ids:
                continue
            row_status = _job_status_value(row.status)
            row_stderr = row.stderr
            if row_status in {JobStatus.PENDING.value, JobStatus.RUNNING.value} and row.created_at:
                age_seconds = (_utcnow() - row.created_at).total_seconds()
                if age_seconds > STALE_JOB_SECONDS:
                    row_status = JobStatus.FAILED.value
                    row_stderr = (row_stderr or "") + ("\n" if row_stderr else "") + "Job did not report completion and was marked interrupted after 10 minutes."
                    try:
                        from db.models import SessionLocal as _SessionLocal
                        cleanup_db = _SessionLocal()
                        row.status = row_status
                        row.stderr = row_stderr
                        row.completed_at = _utcnow()
                        cleanup_db.merge(row)
                        cleanup_db.commit()
                        cleanup_db.close()
                    except Exception as exc:
                        logger.warning("[tool_runs] Could not mark stale job: %s", exc)
            job_list.append({
                "job_id": row.job_id,
                "status": row_status,
                "tool_name": row.tool_name,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                "stdout": row.stdout,
                "stderr": row_stderr,
                "exit_code": row.exit_code if row_status != JobStatus.FAILED.value else (row.exit_code if row.exit_code is not None else -1),
                "arguments": json.loads(row.arguments or "{}"),
                "artifacts": json.loads(row.artifact_paths or "[]"),
            })
    except Exception as exc:
        logger.warning("[tool_runs] Could not load persisted jobs: %s", exc)
    if status:
        job_list = [j for j in job_list if j["status"] == status]
    return [JobResponse(**j) for j in job_list]

@app.delete("/api/jobs/{job_id}", tags=["Execution"], dependencies=[Depends(require_api_key)])
def delete_job(job_id: str):
    """Delete one job from memory and the persisted tool-run history."""
    deleted_job_ids.add(job_id)
    jobs.pop(job_id, None)
    try:
        from db.models import SessionLocal, ToolRun
        db = SessionLocal()
        row = db.get(ToolRun, job_id)
        if row is not None:
            db.delete(row)
            db.commit()
        db.close()
    except Exception as exc:
        logger.warning("[tool_runs] Could not delete persisted job: %s", exc)
    return {"deleted": job_id}

@app.delete("/api/jobs", tags=["Execution"], dependencies=[Depends(require_api_key)])
def clear_jobs():
    """Clear all in-memory and persisted tool-run history."""
    deleted_job_ids.update(jobs.keys())
    jobs.clear()
    try:
        from db.models import SessionLocal, ToolRun
        db = SessionLocal()
        deleted = db.query(ToolRun).delete(synchronize_session=False)
        db.commit()
        db.close()
    except Exception as exc:
        logger.warning("[tool_runs] Could not clear persisted jobs: %s", exc)
        deleted = 0
    return {"deleted": deleted}

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
    """Download a specific report file.

    Path-traversal safe: the candidate path is resolved (following
    symlinks) and must remain inside the platform Reports directory
    before any file is served. Absolute paths, `..` escapes, and
    symlinks pointing outside all resolve to 404.
    """
    reports_root = Path(get_reports_dir()).resolve()
    candidate = (reports_root / report_name).resolve()
    if candidate != reports_root and reports_root not in candidate.parents:
        raise HTTPException(status_code=404, detail=f"Report '{report_name}' not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"Report '{report_name}' not found")
    return FileResponse(
        path=str(candidate),
        filename=candidate.name,
        media_type="text/plain"
    )

@app.get("/api/tool-artifacts/{artifact_path:path}", tags=["Execution"])
def download_tool_artifact(artifact_path: str):
    """Download only files from approved generated-artifact directories."""
    root = Path(get_platform_root()).resolve()
    candidate = (root / artifact_path).resolve()
    allowed_roots = [
        (root / "Reports").resolve(),
        (root / "Data" / "Reports").resolve(),
        (root / "Data" / "Archive").resolve(),
        (root / "Data" / "Active_Workspace").resolve(),
        (root / "Data" / "Exports").resolve(),
    ]
    if not any(allowed == candidate or allowed in candidate.parents for allowed in allowed_roots):
        raise HTTPException(status_code=404, detail="Tool artifact not found")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Tool artifact not found")
    return FileResponse(path=str(candidate), filename=candidate.name)

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

@app.get("/api/db/placeholder-aliases", tags=["Rules"])
def list_placeholder_aliases():
    # List all defined placeholder aliases.
    # Response shape matches what the frontend expects:
    # [{"id", "alias", "fields", "description"}, ...]
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


@app.get("/api/db/placeholder-aliases/suggestions", tags=["Rules"])
def suggest_placeholder_alias_fields(
    limit_events: int = Query(50, ge=1, le=500, description="Number of recent pasted notables to scan"),
):
    """Suggest candidate field names for placeholder aliases from recent pasted notables."""

    # Scans recent SplunkEvent rows with sourcetype="splunk:notable:pasted", extracts the
    # "fields" dict from each event's raw JSON payload, and returns a frequency-ranked
    # list of field names observed. This backs the UI's "Suggest from recent notables"
    # button in the alias editor.
    #
    # Response shape:
    #     {"candidates": [{"field": "host", "count": N}, ...]}
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        try:
            rows = (
                db.query(SplunkEvent)
                .filter(SplunkEvent.sourcetype == "splunk:notable:pasted")
                .order_by(SplunkEvent.ingested_at.desc())
                .limit(limit_events)
                .all()
            )
        finally:
            db.close()

        field_counts: Dict[str, int] = {}
        for row in rows:
            try:
                payload = json.loads(row.raw) if row.raw else {}
            except Exception:
                continue

            fields = payload.get("fields") or {}
            if not isinstance(fields, dict):
                continue

            for name in fields.keys():
                if not name:
                    continue
                key = str(name).strip()
                if not key:
                    continue
                field_counts[key] = field_counts.get(key, 0) + 1

        candidates = [
            {"field": name, "count": count}
            for name, count in sorted(field_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

        return {"candidates": candidates}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/db/placeholder-aliases", tags=["Rules"])
def create_placeholder_alias(payload: PlaceholderAliasPayload):
    # Create a new placeholder alias.
    # Alias names are normalized to lowercase and must be unique.
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

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

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
    # Update an existing placeholder alias.
    # Supports partial updates for alias, fields, and description.
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

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

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
    # Delete a placeholder alias definition.
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

        # Mirror alias definitions to placeholder_aliases.json (best-effort)
        _rebuild_placeholder_aliases_file()

        db.close()

        return {"success": True, "deleted_id": alias_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/db/supportive-queries", tags=["Rules"])
def list_supportive_queries(rule_id: Optional[str] = Query(default=None, description="Filter by logical rule_id")):
    # List supportive SPL queries.
    # When a rule_id is provided, only queries for that rule are returned.
    # Otherwise, all supportive queries are listed. This API backs the
    # analyst-facing editor so supportive queries can be tuned on the fly
    # without touching JSON seed files.
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


@app.post("/api/db/supportive-queries/draft", tags=["Rules"])
def draft_supportive_queries(payload: SupportivePlaybookDraftRequest):
    """Create reviewable, unsaved SPL drafts for a rule without a playbook.

    Drafts intentionally use an explicit index placeholder. They are never
    returned as authoritative investigation cards until an analyst approves
    and saves them through the normal supportive-query editor.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQuery

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == payload.case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {payload.case_id} not found")

        rule_key = (case.rule_id or "").strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        existing = db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_key).count()
        if existing:
            return {"requires_approval": False, "playbook_available": True, "rule_id": case.rule_id, "draft_queries": []}

        source_fields: Dict[str, Any] = {}
        # SplunkEvent stores the promotion linkage inside the raw JSON
        # payload, rather than as a mapped ORM column.
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            if not event.raw:
                continue
            try:
                event_payload = json.loads(event.raw) or {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == payload.case_id:
                source_fields = event_payload.get("fields") or {}
                break

        host = source_fields.get("host") or source_fields.get("destination") or "$host$"
        user = source_fields.get("user") or source_fields.get("username") or "$user$"
        process = source_fields.get("process") or source_fields.get("process_name") or "$process$"
        rule_label = case.rule_name or source_fields.get("correlation_search") or case.rule_id or "unsupported rule"
        anchor = " ".join(str(source_fields.get(key) or "") for key in ("title", "description", "correlation_search", "rule_id", "process", "file_path"))
        quoted_anchor = re.sub(r"[^A-Za-z0-9_.:/ -]", " ", anchor).strip()[:180] or rule_label

        drafts = [
            {
                "rule_id": rule_key,
                "title": "Rule-scoped event context",
                "description": "Review events matching the notable's rule and core entities. Replace the index placeholder before approval.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" ("{quoted_anchor}" OR process="{process}") | table _time host user process parent_process command_line _raw | sort 0 -_time',
            },
            {
                "rule_id": rule_key,
                "title": "Related activity by host and user",
                "description": "Look for adjacent activity by the affected host and identity around the notable time window.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" user="{user}" earliest=-24h | stats count values(process) as processes values(parent_process) as parent_processes values(command_line) as command_lines by host user | sort -count',
            },
            {
                "rule_id": rule_key,
                "title": "Process and destination correlation",
                "description": "Check whether the process or destination appears with related network or execution activity.",
                "spl_query": f'index=<REVIEW_REQUIRED> host="{host}" (process="{process}" OR dest="{source_fields.get("destination_ip") or "$destination_ip$"}") | table _time host user process parent_process dest dest_ip command_line action result | sort 0 -_time',
            },
        ]
        return {
            "requires_approval": True,
            "playbook_available": False,
            "case_id": payload.case_id,
            "rule_id": rule_key,
            "rule_name": rule_label,
            "draft_queries": drafts,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@app.get("/api/db/supportive-queries/status/{case_id}", tags=["Rules"])
def supportive_playbook_status(case_id: str):
    """Report whether a case's rule already has an approved playbook."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQuery

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        source_rule_id = ""
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            if not event.raw:
                continue
            try:
                event_payload = json.loads(event.raw) or {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == case_id:
                source_rule_id = ((event_payload.get("raw_fields") or {}).get("rule_id") or
                                  (event_payload.get("fields") or {}).get("rule_id") or "").strip()
                break

        rule_key = (case.rule_id or source_rule_id).strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        query_count = db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_key).count()
        return {
            "case_id": case_id,
            "rule_id": rule_key,
            "rule_name": case.rule_name,
            "playbook_available": query_count > 0,
            "query_count": query_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@app.post("/api/db/supportive-queries/import-results", tags=["Rules"])
def import_supportive_results(payload: SupportiveResultsImportRequest):
    """Turn analyst-provided Splunk results into reviewable, unsaved SPL drafts."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == payload.case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {payload.case_id} not found")

        records: List[Dict[str, Any]] = []
        text_content = payload.content.strip()
        suffix = (payload.filename or "").lower()
        try:
            parsed = json.loads(text_content) if suffix.endswith(".json") or text_content[:1] in "[{" else None
            if isinstance(parsed, list):
                records = [item for item in parsed if isinstance(item, dict)]
            elif isinstance(parsed, dict):
                for key in ("results", "events", "data", "rows"):
                    if isinstance(parsed.get(key), list):
                        records = [item for item in parsed[key] if isinstance(item, dict)]
                        break
                if not records:
                    records = [parsed]
        except Exception:
            records = []

        if not records and (suffix.endswith(".csv") or any("," in line for line in text_content.splitlines()[:3])):
            try:
                reader = csv.DictReader(io.StringIO(text_content))
                records = [dict(row) for row in reader if row]
            except Exception:
                records = []

        observed: Dict[str, List[str]] = {"index": [], "sourcetype": [], "host": [], "field_names": []}
        def add_observed(bucket: str, value: Any):
            if value is None:
                return
            for item in (value if isinstance(value, list) else [value]):
                value_text = str(item).strip().strip('"')
                if value_text and value_text not in observed[bucket]:
                    observed[bucket].append(value_text)

        for record in records:
            lowered = {str(k).lower(): v for k, v in record.items()}
            add_observed("index", lowered.get("index") or lowered.get("indexes") or lowered.get("search_index"))
            add_observed("sourcetype", lowered.get("sourcetype") or lowered.get("source_type") or lowered.get("searchtype"))
            add_observed("host", lowered.get("host") or lowered.get("dest") or lowered.get("destination"))
            observed["field_names"].extend(str(k) for k in record.keys() if str(k) not in observed["field_names"])

        for line in text_content.splitlines():
            for key, bucket in (("index", "index"), ("sourcetype", "sourcetype"), ("searchtype", "sourcetype"), ("host", "host")):
                match = re.search(rf"(?:^|[\s,]){key}\s*[:=]\s*[\"']?([^\s,\"']+)", line, re.IGNORECASE)
                if match:
                    add_observed(bucket, match.group(1))

        # Pull the case's core entities from the stored notable.
        source_fields: Dict[str, Any] = {}
        for event in db.query(SplunkEvent).order_by(SplunkEvent.id.desc()).all():
            try:
                event_payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue
            if event_payload.get("promoted_case_id") == payload.case_id:
                source_fields = event_payload.get("fields") or {}
                break
        host = source_fields.get("host") or source_fields.get("destination") or (observed["host"][0] if observed["host"] else "$host$")
        user = source_fields.get("user") or source_fields.get("username") or "$user$"
        process = source_fields.get("process") or source_fields.get("process_name") or "$process$"
        rule_key = (case.rule_id or source_fields.get("rule_id") or "").strip()
        if not rule_key:
            rule_key = re.sub(r"[^a-z0-9]+", "_", (case.rule_name or "unsupported_rule").lower()).strip("_") or "unsupported_rule"
        index_clause = " OR ".join(f'index="{value}"' for value in observed["index"]) or "index=<REVIEW_REQUIRED>"
        sourcetype_clause = " OR ".join(f'sourcetype="{value}"' for value in observed["sourcetype"])
        source_filter = f'({index_clause})' if " OR " in index_clause else index_clause
        if sourcetype_clause:
            source_filter += f" ({sourcetype_clause})"
        drafts = [
            {"rule_id": rule_key, "title": "Observed event context", "description": "Search the observed Splunk data source for the notable's core entities.", "spl_query": f'{source_filter} host="{host}" (process="{process}" OR user="{user}") | table _time host user process parent_process command_line _raw | sort 0 -_time'},
            {"rule_id": rule_key, "title": "Related host and user activity", "description": "Review adjacent activity for the affected host and identity in the imported data source.", "spl_query": f'{source_filter} host="{host}" user="{user}" earliest=-24h | stats count values(process) as processes values(command_line) as command_lines by host user | sort -count'},
            {"rule_id": rule_key, "title": "Process and persistence correlation", "description": "Check for related execution or persistence activity around the notable.", "spl_query": f'{source_filter} host="{host}" (process="{process}" OR file_path="{source_fields.get("file_path") or "$file_path$"}") | table _time host user process parent_process file_path command_line action result | sort 0 -_time'},
        ]
        return {"requires_approval": True, "playbook_available": False, "case_id": payload.case_id, "rule_id": rule_key, "observed": observed, "record_count": len(records), "draft_queries": drafts}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if db is not None:
            db.close()


@app.post("/api/db/supportive-queries", tags=["Rules"])
def create_supportive_query(payload: SupportiveQueryPayload):
    # Create a new supportive SPL query for a correlation rule.
    # IDs are assigned explicitly based on the current max(id) to avoid
    # depending on a potentially misaligned Postgres sequence, mirroring
    # the import logic used by the ES rules importer.
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

        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

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
    # Update an existing supportive SPL query.
    # Supports partial updates; any field omitted from the payload is left
    # unchanged. Rule IDs can be adjusted if needed when re-grouping
    # queries under a different logical rule.
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

        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

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
    # Delete a supportive SPL query.
    # This does not touch stored supportive_query_results; those remain as
    # historical evidence even if the underlying query definition is
    # retired.
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
        
        # Best-effort persist of updated supportive queries to supportive_rules.json
        _rebuild_supportive_rules_file()

        db.close()

        return {"success": True, "deleted_id": query_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Rules & Closure Notes Endpoints
@app.get("/api/db/rules", tags=["Rules"])
def list_rules():
    # List all available ES correlation rules, including any supportive queries.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, ESCorrelationRule, SupportiveQuery
        db = SessionLocal()
        rules = db.query(ESCorrelationRule).filter(ESCorrelationRule.enabled == 1).all()

        # Preload supportive queries for all rules
        all_supportive = db.query(SupportiveQuery).all()
        db.close()

        # Older imported databases can contain enabled rule rows with a
        # missing rule_name. Use the checked-in rule catalog as a read-only
        # display fallback so closure selection remains understandable without
        # requiring a database migration.
        catalog_by_id: Dict[str, Dict[str, Any]] = {}
        for catalog_path in [
            Path(get_platform_root()) / "updated_rules.json",
            Path(__file__).resolve().parent.parent / "updated_rules.json",
        ]:
            try:
                if catalog_path.exists():
                    catalog_payload = json.loads(catalog_path.read_text(encoding="utf-8"))
                    catalog_by_id = {
                        str(item.get("rule_id")): item
                        for item in (catalog_payload.get("rules") or [])
                        if item.get("rule_id")
                    }
                    if catalog_by_id:
                        break
            except Exception:
                continue

        by_rule: Dict[str, List[Dict[str, Any]]] = {}
        for sq in all_supportive:
            by_rule.setdefault(sq.rule_id, []).append({
                "id": sq.id,
                "title": sq.title,
                "description": sq.description,
                "spl_query": sq.spl_query,
            })

        def supportive_for_rule(rule_obj) -> List[Dict[str, Any]]:
            # Return supportive queries for a given rule.
            # In addition to queries explicitly keyed to this rule_id, we
            # support lightweight family sharing for LotL-style rules: any
            # rule whose ID starts with ``lotl_`` automatically inherits the
            # supportive queries defined under the canonical
            # ``lotl_outbound_connection`` family, unless duplicates exist.
            # This lets future LotL notables reuse the same investigation
            # SPL without duplicating query definitions in the database.

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

        response_rules = [
            {
                "rule_id": r.rule_id,
                "rule_name": r.rule_name or catalog_by_id.get(r.rule_id, {}).get("rule_name") or r.rule_id,
                "description": r.description or catalog_by_id.get(r.rule_id, {}).get("description") or "",
                "category": r.category or catalog_by_id.get(r.rule_id, {}).get("category") or "",
                "severity": r.severity or catalog_by_id.get(r.rule_id, {}).get("severity") or "medium",
                "drilldown_fields": json.loads(r.drilldown_fields) if r.drilldown_fields else catalog_by_id.get(r.rule_id, {}).get("drilldown_fields", []),
                "required_closure_fields": json.loads(r.required_closure_fields) if r.required_closure_fields else catalog_by_id.get(r.rule_id, {}).get("required_closure_fields", []),
                "supportive_queries": supportive_for_rule(r),
            }
            for r in rules
        ]

        # Analyst-created unsupported rules do not necessarily have an
        # ESCorrelationRule row yet. Once their reviewed supportive queries
        # are saved, expose them as synthetic rule entries so the analysis UI
        # can resolve the active case and render the new playbook immediately.
        known_rule_ids = {str(item.get("rule_id") or "").strip() for item in response_rules}
        for rule_id, queries in by_rule.items():
            normalized_id = str(rule_id or "").strip()
            if not normalized_id or normalized_id in known_rule_ids:
                continue
            catalog_item = catalog_by_id.get(normalized_id, {})
            fallback_name = normalized_id.replace("_", " ").strip().title() or normalized_id
            response_rules.append({
                "rule_id": normalized_id,
                "rule_name": catalog_item.get("rule_name") or fallback_name,
                "description": catalog_item.get("description") or "Analyst-created supportive playbook rule",
                "category": catalog_item.get("category") or "custom",
                "severity": catalog_item.get("severity") or "medium",
                "drilldown_fields": catalog_item.get("drilldown_fields") or [],
                "required_closure_fields": catalog_item.get("required_closure_fields") or [],
                "supportive_queries": queries,
            })

        return response_rules
    except Exception:
        return []

@app.post("/api/code-review/fix", tags=["AI Analysis"])
def fix_code(payload: dict):
    # Generate fixed/improved version of code based on review.
    try:
        sys.path.insert(0, get_platform_root())
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', '').strip()  # empty = auto-resolve
        
        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")
        
        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")
        
        prompt = (
            "Fix and improve this {language} code. Return ONLY the corrected code in a code block, no explanations:\n\n"
            "```{language}\n"
            "{code}\n"
            "```"
        ).format(language=language, code=code_snippet)
        
        result = client.generate(prompt, model=model)
        
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])
        
        fixed_code = result["response"] or ""
        if "```" in fixed_code:
            parts = fixed_code.split("```")
            if len(parts) >= 2:
                fixed_code = parts[1].replace(f"{language}\n", "", 1).strip()
        
        return {"success": True, "fixed_code": fixed_code, "language": language, "model": model}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/code-review", tags=["AI Analysis"])
def code_review(payload: dict):
    # Review code using local Ollama model and store results in database.

    # Payload shape:
    # {
    #     "code_snippet": "<code to review>",
    #     "language": "python" (optional, defaults to python),
    #     "model": "<tag>" (optional; empty/auto-resolves an installed model)
    #     "instructions": "Optional focus or question for the review"
    # }
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', '').strip()  # empty = auto-resolve
        instructions = payload.get('instructions', '').strip()
        
        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")
        
        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")
        
        # Build review prompt tuned for concrete changes rather than generic commentary.
        focus_block = ""
        if instructions:
            focus_block = f"\nAdditional focus/instructions from the analyst:\n{instructions}\n"

        prompt = (
            f"You are an expert {language} engineer.\n\n"
            "The user has provided a code fragment or file context and may also provide\n"
            "explicit instructions about what to change. Your job is to propose\n"
            "CONCRETE code edits, not a generic data or project review.\n\n"
            "For the code below, respond with:\n"
            "1. A very short summary of what you will change.\n"
            "2. Specific code edits:\n"
            "   - Mention the file or component name when possible.\n"
            "   - Show before/after or replacement snippets as needed.\n"
            "   - Focus on the minimal diff that satisfies the instructions.\n"
            "3. If you suggest config/UI changes (HTML/JS/Python), include the exact\n"
            "   updated snippet ready to paste into the file.\n\n"
            "Avoid broad \"data quality\" or \"potential uses\" essays. Stay focused on\n"
            "actionable code changes and patches.\n"
            f"{focus_block}\n\n"
            "Code:\n"
            f"```{language}\n"
            f"{code_snippet}\n"
            "```\n"
        )
        
        result = client.generate(prompt, model=model)
        
        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])
        
        review_text = result["response"] or ""
        
        # Store in database
        db = SessionLocal()
        code_review = CodeReview(
            code_snippet=code_snippet,
            language=language,
            review_result=review_text,
            model_name=model
        )
        db.add(code_review)
        db.commit()
        db.refresh(code_review)
        db.close()
        
        return {
            "success": True,
            "review_id": code_review.id,
            "language": language,
            "model": model,
            "review": review_text,
            "created_at": code_review.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/code-review/sections", tags=["AI Analysis"])
def code_review_sections(payload: dict):
    # Detect functions/classes/sections in a code snippet.
    # This is a lightweight helper for the Code Review UI and does not
    # call any models. It simply inspects the code and returns structural
    # sections so the frontend can focus reviews on specific functions or
    # classes without manual search terms.
    try:
        code_snippet = (payload.get("code_snippet") or "").strip()
        language = (payload.get("language") or "python").strip()

        if not code_snippet:
            raise HTTPException(status_code=400, detail="code_snippet is required")

        sections = _extract_code_sections(code_snippet, language)

        return {
            "language": language,
            "sections": sections,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/code-review/zip", tags=["AI Analysis"])
async def code_review_zip(
    file: UploadFile = File(...),
    language: str = Query("python"),
    model: str = Query(""),  # empty = auto-resolve installed model
    instructions: str = Query("", description="Optional focus or question for the review"),
):
    # Review a zipped project using the local Ollama model.
    # This endpoint accepts a .zip archive, extracts a curated subset of
    # text/code files, concatenates representative snippets, and forwards
    # the aggregated project context into the standard code_review flow.
    try:
        filename = (file.filename or "").lower()
        if not filename.endswith(".zip"):
            raise HTTPException(status_code=400, detail="Uploaded file must be a .zip archive")

        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Uploaded archive is empty")

        # Sensible limits to avoid overwhelming the model context
        max_total_chars = 20000
        max_per_file_chars = 2000

        allowed_exts = (
            ".py",
            ".js",
            ".ts",
            ".go",
            ".sh",
            ".sql",
            ".html",
            ".htm",
            ".css",
            ".json",
            ".md",
        )

        excluded_paths = [
            "node_modules/",
            "venv/",
            "env/",
            "__pycache__/",
            ".git/",
            "dist/",
            "build/",
        ]

        aggregated_chunks: list[str] = []
        total_chars = 0

        try:
            with zipfile.ZipFile(io.BytesIO(contents)) as zf:
                for name in sorted(zf.namelist()):
                    # Skip directories and obviously unwanted paths
                    if name.endswith("/"):
                        continue
                    lower_name = name.lower()
                    if any(excl in lower_name for excl in excluded_paths):
                        continue
                    if not any(lower_name.endswith(ext) for ext in allowed_exts):
                        continue

                    try:
                        with zf.open(name) as f:
                            raw_bytes = f.read()
                    except Exception:
                        continue

                    try:
                        text = raw_bytes.decode("utf-8", errors="ignore")
                    except Exception:
                        continue

                    if not text.strip():
                        continue

                    snippet = text[:max_per_file_chars]
                    chunk = f"File: {name}\n" + snippet.strip() + "\n\n"

                    if total_chars + len(chunk) > max_total_chars:
                        break

                    aggregated_chunks.append(chunk)
                    total_chars += len(chunk)
        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid or corrupted zip archive")

        if not aggregated_chunks:
            raise HTTPException(status_code=400, detail="No supported text/code files found in archive")

        project_summary = "\n".join(aggregated_chunks)

        # Reuse the existing code review pipeline to store and analyze
        payload = {
            "code_snippet": project_summary,
            "language": language,
            "model": model,
            "instructions": instructions,
        }
        return code_review(payload)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/code-reviews", tags=["AI Analysis"])
def list_code_reviews(limit: int = Query(20, ge=1, le=100)):
    # List recent code reviews from database.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        
        db = SessionLocal()
        reviews = db.query(CodeReview).order_by(
            CodeReview.created_at.desc()
        ).limit(limit).all()
        db.close()
        
        return [
            {
                "id": r.id,
                "language": r.language,
                "model": r.model_name,
                "code_snippet": r.code_snippet[:500],  # First 500 chars
                "review": r.review_result[:1000],  # First 1000 chars
                "created_at": r.created_at.isoformat()
            }
            for r in reviews
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/code-reviews/{review_id}", tags=["AI Analysis"])
def get_code_review(review_id: int):
    # Get a specific code review by ID.
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        
        db = SessionLocal()
        review = db.query(CodeReview).filter(CodeReview.id == review_id).first()
        db.close()
        
        if not review:
            raise HTTPException(status_code=404, detail=f"Review {review_id} not found")
        
        return {
            "id": review.id,
            "language": review.language,
            "model": review.model_name,
            "code_snippet": review.code_snippet,
            "review": review.review_result,
            "created_at": review.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # Global exception handler for unhandled errors.
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
