"""Tools surface: tool registry, sync/async execution with in-memory jobs,
reports, tool artifacts, and the ToolRun persistence trail."""
import asyncio
import datetime
import json
import logging
import os
import subprocess
import sys
import uuid
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from sqlalchemy import func

from api import deps
from api.helpers.errors import InternalError, raise_internal
from api.helpers import admission

from api.auth import require_api_key, require_role
from api.schemas import JobResponse, JobStatus, ToolInfo, ToolRequest
from db.util import utcnow_naive

logger = logging.getLogger("soc.api")

router = APIRouter()

# In-memory job tracking (in production, use Redis)
jobs: Dict[str, Dict[str, Any]] = {}
deleted_job_ids = set()
STALE_JOB_SECONDS = 600

# C2.1.2 hardening (DEVELOPMENT_PLAN §11, signed off Sept 30): cap the
# in-memory job queue so a runaway client cannot grow it without bound.
# Counts unfinished (pending/running) jobs; over-limit admission answers 429.
JOB_QUEUE_MAX = int(os.environ.get("JOB_QUEUE_MAX", "100"))

# API security review, optimization #3: bound simultaneous tool
# subprocesses SEPARATELY from the job-queue cap above. The queue may
# admit up to JOB_QUEUE_MAX unfinished jobs, but every RUNNING job used
# to occupy an anyio threadpool thread (default pool: 40 tokens) for
# its whole subprocess run — up to 300 s each — so ~40+ concurrent
# runs exhausted the pool and sync endpoints starved. Background tasks
# now park on this running-semaphore (see _tool_run_semaphore): waiting
# for a permit costs no threadpool token, and only the subprocess run
# itself, under a permit, occupies one. The queue stays at 100;
# simultaneous subprocesses stay at TOOL_CONCURRENCY_MAX.
TOOL_CONCURRENCY_MAX = max(1, int(os.environ.get("TOOL_CONCURRENCY_MAX", "6")))

# Permit pools, one per event loop. asyncio primitives bind to the loop
# they are first used on, and test clients spin up a fresh loop per
# portal — a single module-level semaphore would raise "bound to a
# different event loop" on the second client. In production there is
# exactly one loop, i.e. one shared pool of TOOL_CONCURRENCY_MAX permits.
_tool_run_semaphores = weakref.WeakKeyDictionary()


def _tool_run_semaphore() -> "asyncio.Semaphore":
    """The tool-running permit pool for the current event loop."""
    loop = asyncio.get_running_loop()
    semaphore = _tool_run_semaphores.get(loop)
    if semaphore is None:
        semaphore = asyncio.Semaphore(TOOL_CONCURRENCY_MAX)
        _tool_run_semaphores[loop] = semaphore
    return semaphore

# C2.1.6 hardening: registered tools run on an explicit env allowlist,
# NOT a copy of the API environment — the wholesale copy handed every
# registered tool the API process's secrets (DATABASE_URL, API_KEY,
# OLLAMA_API_KEY, ...). An audit of Tools/**/*.py found tools read
# exactly one variable from their environment (COMMANDER_BOOT, the
# interactive-prompt guard); anything else a tool genuinely needs must
# be named here explicitly.
_TOOL_ENV_PASSTHROUGH: Tuple[str, ...] = ()


def _tool_env() -> Dict[str, str]:
    """Minimal environment for a registered tool subprocess.

    Allowlist, not inheritance: PATH (helper binaries tools shell
    out to), HOME (tooling cache location), and COMMANDER_BOOT (the
    API-boot flag that suppresses terminal-only prompts so API jobs
    never block waiting for a human). Secrets of the API process
    stay unreadable by tools.
    """
    env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", os.defpath),
        "COMMANDER_BOOT": "1",
    }
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    for key in _TOOL_ENV_PASSTHROUGH:
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    return env

def _job_status_value(status: Any) -> str:
    """Normalize enum instances and legacy persisted enum strings."""
    if isinstance(status, JobStatus):
        return status.value
    value = str(status or "").strip()
    if value.startswith("JobStatus."):
        value = value.split(".", 1)[1].lower()
    return value.lower()


def _unfinished_job_count() -> int:
    """Unfinished (pending/running) jobs across BOTH stores: the
    process-local jobs dict AND the persisted ToolRun rows.

    The dict is process-local, so a daemon restart (or a second
    worker) would otherwise see an empty queue and the C2.1.2
    admission cap would silently reset to zero while persisted
    jobs keep occupying slots. The persisted tally is a DB-side
    func.count() filtered on unfinished statuses (API security
    review optimization #4) — one aggregate, never a fetch of
    every ToolRun row. Statuses are normalized in SQL with the
    same prefix-strip + lowercase _job_status_value applies, so
    legacy persisted enum strings still count, and job ids
    already counted from the in-memory dict are excluded so a
    job present in BOTH stores counts once. A DB failure
    degrades to the in-memory count so a database hiccup cannot
    fail admission outright.
    """
    unfinished_statuses = (JobStatus.PENDING.value, JobStatus.RUNNING.value)
    live_unfinished_ids = {
        job_id
        for job_id, job in jobs.items()
        if job.get("status") in unfinished_statuses
    }
    count = len(live_unfinished_ids)
    try:
        from db.models import SessionLocal, ToolRun
        db = SessionLocal()
        try:
            # Same normalization _job_status_value applies to a
            # persisted string: strip the legacy "JobStatus."
            # enum prefix, trim, lowercase — done in SQL so the
            # count never ships rows to the process.
            normalized_status = func.lower(
                func.trim(func.replace(ToolRun.status, "JobStatus.", ""))
            )
            query = db.query(func.count(ToolRun.job_id)).filter(
                normalized_status.in_(unfinished_statuses)
            )
            if live_unfinished_ids:
                # A job tracked in BOTH stores must count once.
                query = query.filter(ToolRun.job_id.notin_(live_unfinished_ids))
            count += query.scalar() or 0
        finally:
            db.close()
    except Exception as exc:
        logger.warning("[tool_runs] Admission could not count persisted jobs: %s", exc)
    return count

def load_registry() -> List[Dict]:
    """Load tool registry from JSON."""
    registry_path = os.path.join(deps.get_platform_root(), 'Commander_Registry.json')
    if not os.path.exists(registry_path):
        return []
    with open(registry_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def _fix_tool_path(tool_path: str) -> str:
    """Resolve registry paths from either absolute or repo-relative form."""
    platform_root = deps.get_platform_root()
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

# Legacy artifact sweep (optimization #5 fallback): tools that have not
# declared their output locations in the registry keep the full sweep so
# artifact detection never narrows silently.
_LEGACY_ARTIFACT_DIRS = (
    ("Reports",),
    ("Data", "Reports"),
    ("Data", "Archive"),
    ("Data", "Active_Workspace"),
    ("Data", "Exports"),
)


def _legacy_artifact_candidates(root: str) -> List[str]:
    return [os.path.join(root, *parts) for parts in _LEGACY_ARTIFACT_DIRS]


def _registry_output_dirs(tool_name: Optional[str]) -> List[str]:
    """Declared output locations for one tool, from the registry.

    Registry entries carry `output_dirs` (relative to the platform root
    unless absolute), seeded from `# OUTPUT_DIR:` headers by the tool
    indexer. A missing/unparsable registry degrades to no declarations —
    tool execution must not fail over metadata.
    """
    if not tool_name:
        return []
    try:
        registry = load_registry()
    except Exception:
        return []
    wanted = str(tool_name).strip().lower()
    for entry in registry:
        if str(entry.get("name", "")).strip().lower() == wanted:
            dirs = entry.get("output_dirs") or []
            return [str(d) for d in dirs if str(d or "").strip()]
    return []


def _resolve_output_dirs(declared: List[str], root: str) -> List[str]:
    """Resolve declared output locations, refusing escapes from the root.

    Relative declarations join the platform root; absolutes are honored
    only when already inside it. Containment is checked on realpath (so
    symlinks cannot smuggle the snapshot outside) — the same
    traversal-stance as the reports/artifact endpoints (#15). Declared
    lists that resolve to nothing fall back to the legacy sweep at the
    caller.
    """
    real_root = os.path.realpath(root)
    resolved = []
    for declared_dir in declared:
        candidate = (
            declared_dir if os.path.isabs(declared_dir) else os.path.join(root, declared_dir)
        )
        real = os.path.realpath(candidate)
        if real == real_root or real.startswith(real_root + os.sep):
            resolved.append(candidate)
    return resolved


def _tool_artifact_snapshot(tool_name: Optional[str] = None) -> set:
    """Return files that tool runs may create, using portable paths.

    Optimization #5: the before/after diff no longer sweeps all five
    candidate trees for every run. A tool whose registry entry declares
    `output_dirs` is snapshotted against only those directories; the
    legacy sweep remains the fallback for undeclared tools (and for
    declarations that resolve to nothing), so narrowing is opt-in per
    tool and never silently loses artifact detection.
    """
    root = deps.get_platform_root()
    candidates = _legacy_artifact_candidates(root)
    declared = _registry_output_dirs(tool_name)
    if declared:
        scoped = _resolve_output_dirs(declared, root)
        if scoped:
            candidates = scoped
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


def execute_tool_sync(
    tool_path: str,
    args: Dict[str, str],
    silent: bool = False,
    tool_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a tool synchronously and return stdout/stderr/exit_code.

    `tool_name` scopes the artifact snapshot to the tool's registry-
    declared output dirs (optimization #5); None keeps the legacy sweep.
    """
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
    before = _tool_artifact_snapshot(tool_name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=deps.get_platform_root(),
            env=_tool_env(),
        )
        after = _tool_artifact_snapshot(tool_name)
        artifacts = [os.path.relpath(p, deps.get_platform_root()).replace(os.sep, "/") for p in sorted(after - before)]
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

async def execute_tool_async(
    job_id: str,
    tool_path: str,
    args: Dict[str, str],
    silent: bool = False,
    tool_name: Optional[str] = None,
):
    """Background task to execute tool asynchronously.

    A coroutine on purpose: FastAPI awaits coroutine background tasks on
    the event loop instead of handing them to the threadpool, so the job
    can park on the running-semaphore without holding a threadpool thread
    (optimization #3 — a 100-job queue of 300 s runs must not consume
    the ~40 anyio tokens sync endpoints depend on). The blocking
    subprocess run is dispatched to the threadpool only while a permit
    is held, and the permit is released however the run ends.
    """
    if job_id in deleted_job_ids or job_id not in jobs:
        return
    semaphore = _tool_run_semaphore()
    await semaphore.acquire()
    try:
        # Re-check: the job may have been deleted while it waited for
        # a permit — a cancelled job must never start its subprocess.
        if job_id in deleted_job_ids or job_id not in jobs:
            return
        jobs[job_id]["status"] = JobStatus.RUNNING.value
        result = await run_in_threadpool(execute_tool_sync, tool_path, args, silent, tool_name)
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
        await run_in_threadpool(_persist_tool_run, jobs[job_id])
    finally:
        semaphore.release()


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



@router.get("/api/tools", response_model=List[ToolInfo], tags=["Tools"],
           dependencies=[Depends(require_role("admin"))])
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

@router.get("/api/tools/{tool_name}", response_model=ToolInfo, tags=["Tools"],
           dependencies=[Depends(require_role("admin"))])
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

@router.post("/api/tools/regression", tags=["Tools"],
             dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
def run_tool_catalog_regression():
    """Run safe offline regression checks for the registered tool catalog."""
    runner = os.path.join(deps.get_platform_root(), "scripts", "run_tool_catalog_regression.py")
    if not os.path.exists(runner):
        raise HTTPException(status_code=404, detail="Catalog regression runner not found")
    result = subprocess.run(
        [sys.executable, runner, "--json"], cwd=deps.get_platform_root(),
        capture_output=True, text=True, timeout=180,
        env=_tool_env(),
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise InternalError(result.stdout + result.stderr, context="tools")
    payload["exit_code"] = result.returncode
    return payload

@router.post("/api/execute", response_model=JobResponse, tags=["Execution"],
             dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
def execute_tool(request: ToolRequest, background_tasks: BackgroundTasks):
    """Execute a tool asynchronously and return a job ID."""
    # C2.1.2: admission check before registry/disk work — under load this is
    # the cheap first gate. Counts in-memory AND persisted unfinished
    # jobs so a restart does not reset the cap.
    rejection = admission.queue_rejection(
        _unfinished_job_count(), JOB_QUEUE_MAX
    )
    if rejection is not None:
        raise rejection
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
        request.silent,
        request.tool_name
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

@router.delete("/api/jobs/{job_id}", tags=["Execution"],
               dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
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

@router.delete("/api/jobs", tags=["Execution"],
               dependencies=[Depends(require_api_key), Depends(require_role("admin"))])
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
    reports_dir = deps.get_reports_dir()
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
    reports_root = Path(deps.get_reports_dir()).resolve()
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
    root = Path(deps.get_platform_root()).resolve()
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

@router.get("/api/registry", tags=["System"], dependencies=[Depends(require_role("admin"))])
def get_registry():
    """Get the full tool registry as JSON."""
    return load_registry()

@router.post("/api/registry/reload", tags=["System"], dependencies=[Depends(require_role("admin"))])
def reload_registry():
    """Force a registry rebuild (runs tool_indexer)."""
    indexer_path = os.path.join(deps.get_platform_root(), 'Tools', 'tool_indexer', 'tool_indexer.py')
    if not os.path.exists(indexer_path):
        raise HTTPException(status_code=400, detail="Tool indexer not found")
    try:
        result = subprocess.run(
            [sys.executable, indexer_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=deps.get_platform_root(),
            env=_tool_env(),
        )
        return {
            "status": "success" if result.returncode == 0 else "failed",
            "message": result.stdout + result.stderr
        }
    except Exception as e:
        raise_internal(e, context="tools")

