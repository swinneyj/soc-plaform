"""HTTP proxy from the main backend to the isolated tools-worker.

Active only when TOOLS_WORKER_URL is set (docker-compose). Otherwise
routes fall back to in-process execution so hermetic tests and local
`scripts/dev` (no worker) behave exactly as before.

Auth stays on the main backend: routes enforce require_api_key /
require_role BEFORE calling here. The worker trusts the internal
network and enforces nothing itself.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Dict, List, Optional

import requests
from fastapi import HTTPException

HMAC_WINDOW_SECONDS = 60


def worker_base() -> Optional[str]:
    base = (os.environ.get("TOOLS_WORKER_URL") or "").strip().rstrip("/")
    return base or None


def is_enabled() -> bool:
    return worker_base() is not None


def _hmac_secrets() -> List[str]:
    """Configured HMAC secrets: primary first, secondary for rotation.

    Mirrors tools_worker/app.py. Either secret verifies on the worker;
    this proxy always signs with the primary. Rotation: set the new
    secret as TOOLS_WORKER_HMAC_SECRET_SECONDARY on both sides, deploy,
    then promote it to TOOLS_WORKER_HMAC_SECRET and clear secondary.
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


def _auth_headers() -> Dict[str, str]:
    secrets = _hmac_secrets()
    if not secrets:
        return {}
    ts = str(int(time.time()))
    sig = hmac.new(secrets[0].encode(), ts.encode(), hashlib.sha256).hexdigest()
    return {"X-Tools-Timestamp": ts, "X-Tools-Signature": sig}


def _call(
    method: str,
    path: str,
    payload: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, str]] = None,
    timeout: float = 65.0,
) -> Any:
    base = worker_base()
    assert base is not None
    url = base + path
    headers: Dict[str, str] = {}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    headers.update(_auth_headers())
    try:
        resp = requests.request(
            method.upper(), url, params=params, json=payload,
            headers=headers, timeout=timeout,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"tools-worker unreachable: {exc}"
        )
    if resp.status_code >= 400:
        try:
            parsed = resp.json()
            if isinstance(parsed, dict):
                detail_text = parsed.get("detail", resp.text)
            else:
                detail_text = resp.text
        except Exception:
            detail_text = resp.text or f"tools-worker HTTP {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=detail_text)
    try:
        if not resp.text.strip():
            return None
        return resp.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"tools-worker unreachable: {exc}"
        )


def list_tools() -> List[Dict[str, Any]]:
    return _call("GET", "/tools", timeout=15.0)


def get_tool(tool_name: str) -> Dict[str, Any]:
    return _call("GET", f"/tools/{requests.utils.quote(tool_name, safe='')}", timeout=15.0)


def execute_tool(
    tool_name: str,
    arguments: Dict[str, str],
    silent: bool,
    requires_approval: bool = False,
    approved: bool = False,
) -> Dict[str, Any]:
    return _call(
        "POST",
        "/execute",
        payload={
            "tool_name": tool_name,
            "arguments": arguments,
            "silent": silent,
            "requires_approval": requires_approval,
            "approved": approved,
        },
        timeout=15.0,
    )


def get_job(job_id: str) -> Dict[str, Any]:
    return _call("GET", f"/jobs/{requests.utils.quote(job_id, safe='')}", timeout=15.0)


def list_jobs() -> List[Dict[str, Any]]:
    return _call("GET", "/jobs", timeout=15.0)


def delete_job(job_id: str) -> Dict[str, Any]:
    return _call("DELETE", f"/jobs/{requests.utils.quote(job_id, safe='')}", timeout=15.0)


def clear_jobs() -> Dict[str, Any]:
    return _call("DELETE", "/jobs", timeout=15.0)


def reload_registry() -> Dict[str, Any]:
    return _call("POST", "/registry/reload", payload={}, timeout=70.0)
