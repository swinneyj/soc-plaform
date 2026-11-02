"""Tools worker app — isolated FastAPI service that runs Tools/*.py.

Owns: tool registry reads, subprocess execution, in-memory jobs.
Has: no API keys, no DATABASE_URL, no auth. Trusts the internal
compose network; the main backend enforces require_api_key /
require_role BEFORE proxying here.

Env allowlist is intentional: PATH + HOME + COMMANDER_BOOT only.
Anything else a tool genuinely needs must be named explicitly.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import time
import uuid
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

logger = logging.getLogger("tools-worker")

PLATFORM_ROOT = os.environ.get("SOC_PLATFORM_ROOT") or str(
    Path(__file__).resolve().parents[1]
)
if PLATFORM_ROOT not in sys.path:
    sys.path.insert(0, PLATFORM_ROOT)
TOOLS_DIR = os.path.join(PLATFORM_ROOT, "Tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

app = FastAPI(title="SOC Tools Worker", version="1.0.0")

jobs: Dict[str, Dict[str, Any]] = {}

JOB_QUEUE_MAX = max(1, int(os.environ.get("JOB_QUEUE_MAX", "100")))
TOOL_CONCURRENCY_MAX = max(1, int(os.environ.get("TOOL_CONCURRENCY_MAX", "6")))

_tool_run_semaphores: "weakref.WeakKeyDictionary[Any, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _tool_run_semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _tool_run_semaphores.get(loop)
    if sem is None:
        sem = asyncio.Semaphore(TOOL_CONCURRENCY_MAX)
        _tool_run_semaphores[loop] = sem
    return sem


def _tool_env() -> Dict[str, str]:
    # No secrets by construction: never copy os.environ wholesale.
    # Tools in this repo read exactly one variable (COMMANDER_BOOT).
    env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", os.defpath),
        "COMMANDER_BOOT": "1",
    }
    if os.environ.get("HOME"):
        env["HOME"] = os.environ["HOME"]
    return env


def _registry_path() -> str:
    return os.path.join(PLATFORM_ROOT, "Commander_Registry.json")


def load_registry() -> List[Dict[str, Any]]:
    path = _registry_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# Artifact snapshot (ported from api/routes/tools.py optimization #5):
# before/after diff scoped to the tool's registry-declared output_dirs,
# legacy full sweep as fallback. Worker needs this so proxied runs still
# report artifacts even though execution lives off the main backend.
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
    candidates = _legacy_artifact_candidates(PLATFORM_ROOT)
    declared = _registry_output_dirs(tool_name)
    if declared:
        scoped = _resolve_output_dirs(declared, PLATFORM_ROOT)
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


def _fix_tool_path(tool_path: str) -> str:
    if not os.path.isabs(tool_path):
        rel = os.path.join(PLATFORM_ROOT, tool_path)
        if os.path.exists(rel):
            return rel
    normalized = tool_path.replace("\\", "/")
    if "Tools/" in normalized:
        rel_part = normalized.split("Tools/")[-1]
        rel = os.path.join(PLATFORM_ROOT, "Tools", rel_part.replace("/", os.sep))
        if os.path.exists(rel):
            return rel
    return tool_path


# Tools that touch the platform database (direct SessionLocal import or via
# services/splunk_boundary). They must run on the main backend where
# DATABASE_URL lives — never in this secret-free worker. Registry entries
# can also set requires_db via the indexer's `# REQUIRES_DB:` header;
# this fallback covers the known tools until they are reindexed.
DB_TOOLS = frozenset({
    "bulk_rule_matcher",
    "es_rules_importer",
    "splunk_csv_ingestor",
})


def _entry_requires_db(entry: Dict[str, Any]) -> bool:
    if bool(entry.get("requires_db", False)):
        return True
    return str(entry.get("name", "")).strip().lower() in DB_TOOLS


HMAC_WINDOW_SECONDS = 60


def _hmac_secrets() -> List[str]:
    """Configured HMAC secrets: primary first, secondary for rotation.

    Zero-downtime rotation: set the new secret as
    TOOLS_WORKER_HMAC_SECRET_SECONDARY on both sides, deploy, then
    promote it to TOOLS_WORKER_HMAC_SECRET and clear the secondary.
    Either secret verifies; signing always uses the primary (first).
    """
    seen: List[str] = []
    for key in ("TOOLS_WORKER_HMAC_SECRET", "TOOLS_WORKER_HMAC_SECRET_SECONDARY"):
        value = (os.environ.get(key) or "").strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _hmac_secret() -> str:
    """Primary HMAC secret (back-compat: first of _hmac_secrets())."""
    secrets = _hmac_secrets()
    return secrets[0] if secrets else ""


def _sign(timestamp: str, secret: str) -> str:
    return hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).hexdigest()


def verify_worker_auth(request: Request) -> None:
    """60s HMAC check on every mutating/read endpoint except /health.

    Open only when no shared secret is set (local dev + hermetic
    tests). With secrets set: require X-Tools-Timestamp within ±60s
    and X-Tools-Signature == HMAC(secret, timestamp) for either the
    primary or the secondary rotation secret, time-safe compare.
    """
    secrets = _hmac_secrets()
    if not secrets:
        logger.warning("tools-worker HMAC secret unset — accepting unsigned requests (dev/test only)")
        return
    ts = (request.headers.get("x-tools-timestamp") or "").strip()
    sig = (request.headers.get("x-tools-signature") or "").strip()
    if not ts or not sig:
        raise HTTPException(status_code=401, detail="Missing worker auth headers")
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid worker timestamp")
    if abs(time.time() - ts_int) > HMAC_WINDOW_SECONDS:
        raise HTTPException(status_code=401, detail="Worker auth timestamp expired")
    for secret in secrets:
        if hmac.compare_digest(_sign(ts, secret), sig):
            return
    raise HTTPException(status_code=403, detail="Invalid worker signature")


class ExecuteRequest(BaseModel):
    tool_name: str
    arguments: Optional[Dict[str, str]] = None
    silent: bool = False
    requires_approval: bool = False
    approved: bool = False


def execute_tool_sync(
    tool_path: str, args: Dict[str, str], silent: bool = False,
    tool_name: Optional[str] = None,
) -> Dict[str, Any]:
    cmd = [sys.executable, tool_path]
    boolean_flags = {"silent", "list", "use_cases", "replace_all_supportive"}
    for key, val in (args or {}).items():
        if key in boolean_flags:
            if val is True or str(val).strip().lower() in {"1", "true", "yes", "on"}:
                cmd.append(f"--{key.replace('_', '-')}")
        elif val:
            cmd.extend([f"--{key}", str(val)])
    before = _tool_artifact_snapshot(tool_name)
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=PLATFORM_ROOT,
            env=_tool_env(),
        )
        after = _tool_artifact_snapshot(tool_name)
        artifacts = [
            os.path.relpath(p, PLATFORM_ROOT).replace(os.sep, "/")
            for p in sorted(after - before)
        ]
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
    except Exception as exc:  # never fail the worker on a spawn error
        return {"stdout": "", "stderr": str(exc), "exit_code": -1, "artifacts": []}


async def _run_job(
    job_id: str, tool_path: str, args: Dict[str, str], silent: bool,
    tool_name: Optional[str] = None,
):
    if job_id not in jobs:
        return
    sem = _tool_run_semaphore()
    await sem.acquire()
    try:
        if job_id not in jobs:
            return
        jobs[job_id]["status"] = "running"
        result = await run_in_threadpool(
            execute_tool_sync, tool_path, args, silent, tool_name
        )
        if job_id not in jobs:
            return
        jobs[job_id].update(
            {
                "status": "completed" if result["exit_code"] == 0 else "failed",
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "exit_code": result["exit_code"],
                "artifacts": result.get("artifacts", []),
                "completed_at": __import__("datetime").datetime.utcnow().isoformat(),
            }
        )
    finally:
        sem.release()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "tools-worker"}


@app.get("/tools")
def list_tools(request: Request):
    verify_worker_auth(request)
    out = []
    for t in load_registry():
        out.append(
            {
                "name": t["name"],
                "file_name": t.get("file_name", ""),
                "category": t.get("category", "Uncategorized"),
                "description": t.get("description", "No description"),
                "path": _fix_tool_path(t["path"]),
                "arguments": t.get("arguments", []),
                "requires_approval": bool(t.get("requires_approval", False)),
                "requires_db": _entry_requires_db(t),
            }
        )
    return out


@app.get("/tools/{tool_name}")
def get_tool(tool_name: str, request: Request):
    verify_worker_auth(request)
    registry = load_registry()
    tool = next(
        (t for t in registry if str(t["name"]).lower() == tool_name.lower()), None
    )
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    return {
        "name": tool["name"],
        "file_name": tool.get("file_name", ""),
        "category": tool.get("category", "Uncategorized"),
        "description": tool.get("description", "No description"),
        "path": _fix_tool_path(tool["path"]),
        "arguments": tool.get("arguments", []),
        "requires_approval": bool(tool.get("requires_approval", False)),
        "requires_db": _entry_requires_db(tool),
    }


@app.post("/execute", status_code=202)
async def execute_tool(req: ExecuteRequest, request: Request):
    verify_worker_auth(request)
    if req.requires_approval and not req.approved:
        raise HTTPException(status_code=403, detail="Tool requires approval before running")
    unfinished = sum(
        1 for j in jobs.values() if j.get("status") in ("pending", "running")
    )
    if unfinished >= JOB_QUEUE_MAX:
        raise HTTPException(
            status_code=429,
            detail=f"Job queue is full ({unfinished} unfinished >= {JOB_QUEUE_MAX}); retry later",
        )
    registry = load_registry()
    tool = next(
        (t for t in registry if str(t["name"]).lower() == req.tool_name.lower()), None
    )
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{req.tool_name}' not found")
    if _entry_requires_db(tool):
        raise HTTPException(
            status_code=409,
            detail=f"Tool '{req.tool_name}' requires the database and runs on the main backend, not the worker",
        )
    tool_path = _fix_tool_path(tool["path"])
    if not os.path.exists(tool_path):
        raise HTTPException(status_code=400, detail=f"Tool script not found: {tool_path}")
    job_id = str(uuid.uuid4())
    import datetime as _dt

    jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "tool_name": req.tool_name,
        "created_at": _dt.datetime.utcnow().isoformat(),
        "completed_at": None,
        "stdout": None,
        "stderr": None,
        "exit_code": None,
        "arguments": req.arguments or {},
        "artifacts": [],
    }
    asyncio.create_task(
        _run_job(job_id, tool_path, req.arguments or {}, req.silent, req.tool_name)
    )
    return jobs[job_id]


@app.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    verify_worker_auth(request)
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@app.get("/jobs")
def list_jobs(request: Request):
    verify_worker_auth(request)
    return list(jobs.values())


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request):
    verify_worker_auth(request)
    jobs.pop(job_id, None)
    return {"deleted": job_id}


@app.delete("/jobs")
def clear_jobs(request: Request):
    verify_worker_auth(request)
    count = len(jobs)
    jobs.clear()
    return {"deleted": count}


@app.post("/registry/reload")
def reload_registry(request: Request):
    verify_worker_auth(request)
    indexer_path = os.path.join(PLATFORM_ROOT, "Tools", "tool_indexer", "tool_indexer.py")
    if not os.path.exists(indexer_path):
        raise HTTPException(status_code=400, detail="Tool indexer not found")
    try:
        result = subprocess.run(
            [sys.executable, indexer_path],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=PLATFORM_ROOT,
            env=_tool_env(),
        )
        return {
            "status": "success" if result.returncode == 0 else "failed",
            "message": result.stdout + result.stderr,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
