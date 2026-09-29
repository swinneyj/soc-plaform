"""Tools surface: tool registry, sync/async execution with in-memory jobs,
reports, tool artifacts, and the ToolRun persistence trail."""
import datetime
import json
import logging
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from core_lib.utils import get_platform_root, get_reports_dir

from api.auth import require_api_key
from api.schemas import JobResponse, JobStatus, ToolInfo, ToolRequest
from db.util import utcnow_naive

logger = logging.getLogger("soc.api")

router = APIRouter()

# In-memory job tracking (in production, use Redis)
jobs: Dict[str, Dict[str, Any]] = {}
deleted_job_ids = set()
STALE_JOB_SECONDS = 600

def _job_status_value(status: Any) -> str:
    """Normalize enum instances and legacy persisted enum strings."""
    if isinstance(status, JobStatus):
        return status.value
    value = str(status or "").strip()
    if value.startswith("JobStatus."):
        value = value.split(".", 1)[1].lower()
    return value.lower()


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
        "completed_at": utcnow_naive().isoformat()
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



@router.get("/api/tools", response_model=List[ToolInfo], tags=["Tools"])
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

@router.get("/api/tools/{tool_name}", response_model=ToolInfo, tags=["Tools"])
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

@router.post("/api/tools/regression", tags=["Tools"], dependencies=[Depends(require_api_key)])
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

@router.post("/api/execute", response_model=JobResponse, tags=["Execution"], dependencies=[Depends(require_api_key)])
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
        "created_at": utcnow_naive().isoformat(),
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

@router.get("/api/jobs/{job_id}", response_model=JobResponse, tags=["Execution"])
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

@router.get("/api/jobs", response_model=List[JobResponse], tags=["Execution"])
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
                age_seconds = (utcnow_naive() - row.created_at).total_seconds()
                if age_seconds > STALE_JOB_SECONDS:
                    row_status = JobStatus.FAILED.value
                    row_stderr = (row_stderr or "") + ("\n" if row_stderr else "") + "Job did not report completion and was marked interrupted after 10 minutes."
                    try:
                        from db.models import SessionLocal as _SessionLocal
                        cleanup_db = _SessionLocal()
                        row.status = row_status
                        row.stderr = row_stderr
                        row.completed_at = utcnow_naive()
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

@router.delete("/api/jobs/{job_id}", tags=["Execution"], dependencies=[Depends(require_api_key)])
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

@router.delete("/api/jobs", tags=["Execution"], dependencies=[Depends(require_api_key)])
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

@router.get("/api/reports", tags=["Data"])
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

@router.get("/api/reports/{report_name}", tags=["Data"])
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

@router.get("/api/tool-artifacts/{artifact_path:path}", tags=["Execution"])
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

@router.get("/api/registry", tags=["System"])
def get_registry():
    """Get the full tool registry as JSON."""
    return load_registry()

@router.post("/api/registry/reload", tags=["System"])
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

