"""
FastAPI service wrapper for SOC Platform.
Exposes tools as REST endpoints with async job queuing and long-running execution support.
Serves web UI at root path. Includes database and AI analysis endpoints.
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks, File, UploadFile, Query, Depends, Header
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
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
import io
import csv
import zipfile
import ast
from pathlib import Path
from enum import Enum

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

from core_lib.utils import get_platform_root, get_reports_dir, get_archive_dir, get_logs_dir
from services.investigation_state import (
    _build_investigation_state,
    _extract_analysis_sections,
    _extract_question_items,
    _infer_disposition_label,
    _is_substantive_evidence_value,
    _parse_json_list,
    _parse_json_object,
    _serialize_investigation_state_record,
    _summarize_evidence_observation,
    _upsert_investigation_state,
    _apply_investigation_state_to_analysis_text,
)
from services.analysis_service import (
    build_analysis_prompt_intro,
    format_evidence_ledger_entries,
    normalize_phase2_text as _normalize_phase2_text,
    sanitize_analysis_text as _sanitize_analysis_text,
)

# _normalize_rule_match_text is the same normalization as
# normalize_phase2_text (lowercase, non-alphanumerics -> spaces); the old
# byte-identical twin here is retired in favor of the single service copy.
_normalize_rule_match_text = _normalize_phase2_text

NL = chr(10)
NL2 = chr(10) + chr(10)

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
# API-key auth for dangerous routes.
#
# The public deployment exposes process-execution and mutation endpoints that
# must never be reachable without a shared secret. Setting API_KEY activates
# gating on those routes; leaving it unset keeps local development friction
# free (localhost-only exposure), matching the previous behavior.
#
# Deliberately NOT gated (read-only / health / static UI): /api/health,
# /api/db/stats and other GETs, the mounted web UI. Gating those can follow
# once the frontend learns to send the key.
# ---------------------------------------------------------------------------
_API_KEY = os.environ.get("API_KEY", "").strip()


def require_api_key(x_api_key: Optional[str] = Header(default=None), api_key: Optional[str] = Query(default=None)) -> None:
    """FastAPI dependency: reject requests unless they present the API key.

    Accepts the key as either the X-API-Key header or an ?api_key= query
    parameter — either one passes. When API_KEY is unset the dependency is a
    no-op so local dev and existing tests keep working unchanged.
    """
    if not _API_KEY:
        return
    if x_api_key == _API_KEY or api_key == _API_KEY:
        return
    raise HTTPException(status_code=401, detail="Invalid or missing API key")


app = FastAPI(
    title="SOC Platform API",
    lifespan=lifespan,
    description="REST API for SOC Orchestration Platform tools and workflows with local AI analysis",
    version="1.0.0",
    docs_url="/docs" if _enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _enable_docs else None,
)

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

# In-memory job tracking (in production, use Redis)
jobs: Dict[str, Dict[str, Any]] = {}
deleted_job_ids = set()
STALE_JOB_SECONDS = 600

# Pydantic Models
class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

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

class AnalyzeRequest(BaseModel):
    case_id: str
    model: str = ""  # empty = auto-resolve an installed model at call time
    context: str = ""
    prior_analysis: str = ""
    analysis_stage: str = "initial"
    analysis_phase: int = 1


PHASE_SPECIFIC_SUPPORTIVE_QUERIES = {
    "linux_ssh_key_creation": [
        {
            "title": "Authorized key ownership and session context",
            "description": "Determine which account, audit session, source address, and command context were associated with the authorized_keys change.",
            "spl_query": "index=nix host=\"$host$\" (\"authorized_keys\" OR \"ssh-keygen\") | rex field=_raw \"name=\\\"(?<file_path>[^\\\"]+)\\\"\" | rex field=_raw \"comm=\\\"(?<command>[^\\\"]+)\\\"\" | rex field=_raw \"acct=\\\"(?<account>[^\\\"]+)\\\"\" | rex field=_raw \"auid=(?<auid>\\d+)\" | rex field=_raw \"ses=(?<session_id>\\d+)\" | rex field=_raw \"addr=(?<src_ip>\\S+)\" | search file_path=\"*/.ssh/authorized_keys*\" OR command=\"ssh-keygen\" | stats earliest(_time) as first_seen latest(_time) as last_seen values(account) as accounts values(auid) as auids values(session_id) as sessions values(src_ip) as source_ips values(command) as commands by host file_path | sort 0 -last_seen"
        },
        {
            "title": "Other suspicious activity on host",
            "description": "Look for additional persistence, privilege, download, or network activity on the affected host around the key modification.",
            "spl_query": "index=nix host=\"$host$\" earliest=-24h (\"authorized_keys\" OR \"ssh-keygen\" OR \"sudo\" OR \"curl\" OR \"wget\" OR \"nc\" OR \"chmod\" OR \"chown\") | rex field=_raw \"comm=\\\"(?<command>[^\\\"]+)\\\"\" | rex field=_raw \"exe=\\\"(?<exe>[^\\\"]+)\\\"\" | rex field=_raw \"name=\\\"(?<file_path>[^\\\"]+)\\\"\" | stats count as events earliest(_time) as first_seen latest(_time) as last_seen values(command) as commands values(exe) as executables values(file_path) as file_paths by host | sort -events"
        }
    ]
}


def _extract_phase2_queries(response_text: str) -> List[Dict[str, Any]]:
    response_text = response_text or ""
    phase2_queries: List[Dict[str, Any]] = []
    start_marker = "PHASE2_QUERIES_JSON_START"
    end_marker = "PHASE2_QUERIES_JSON_END"
    start_idx = response_text.find(start_marker)
    end_idx = response_text.find(end_marker)
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        raw_block = response_text[start_idx + len(start_marker):end_idx]
        arr_start = raw_block.find("[")
        arr_end = raw_block.rfind("]")
        json_block = ""
        if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
            json_block = raw_block[arr_start:arr_end + 1].strip()
        else:
            json_block = raw_block.strip()
        try:
            parsed_block = json.loads(json_block)
            if isinstance(parsed_block, list):
                for idx, item in enumerate(parsed_block, 1):
                    title = ""
                    spl_value = ""
                    desc = ""

                    if isinstance(item, dict):
                        title_keys = ["title", "name", "query_name"]
                        spl_keys = ["spl", "query", "sql", "code"]
                        desc_keys = ["description", "desc", "notes"]

                        for k in title_keys:
                            if k in item and (item.get(k) or "").strip():
                                title = str(item.get(k)).strip()
                                break

                        for k in spl_keys:
                            if k in item and (item.get(k) or "").strip():
                                spl_value = str(item.get(k)).strip()
                                break

                        for k in desc_keys:
                            if k in item and (item.get(k) or "").strip():
                                desc = str(item.get(k)).strip()
                                break
                    elif isinstance(item, str):
                        spl_value = item.strip()
                        title = f"Phase 2 Query {idx}"

                    title = title or f"Phase 2 Query {idx}"
                    if spl_value:
                        phase2_queries.append({
                            "title": title,
                            "spl": spl_value,
                            "description": desc,
                        })
        except Exception:
            phase2_queries = []

    return phase2_queries


def _looks_like_spl_query(query_text: str) -> bool:
    query = (query_text or "").strip().lower()
    if not query:
        return False
    if re.match(r"^select\b", query):
        return False
    spl_markers = ["index=", "|", "sourcetype=", "eventcode=", "tstats", "from datamodel", "search ", "stats ", "table ", "`"]
    return any(marker in query for marker in spl_markers)


def _already_run_supportive_titles(supportive_results=None) -> set:
    """Titles that already have saved investigation evidence for this case."""
    titles = set()
    for item in supportive_results or []:
        title = (item.get("query_title") or "").strip()
        if not title:
            continue
        raw = item.get("raw_result")
        has_result = False
        if isinstance(raw, dict):
            has_result = bool(
                (raw.get("result_text") or "").strip()
                or (raw.get("analyst_summary") or "").strip()
            )
        elif raw:
            has_result = True
        if has_result:
            titles.add(_normalize_phase2_text(title))
    return titles


def _load_data_source_catalog() -> Dict[str, Any]:
    """Load optional data_source_catalog.json from platform root for prompt grounding."""
    candidates = [
        os.path.join(get_platform_root(), "data_source_catalog.json"),
        os.path.join(os.path.dirname(get_platform_root()), "data_source_catalog.json"),
    ]
    for path in candidates:
        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _format_catalog_for_prompt(catalog: Dict[str, Any], rule_id: str = "") -> str:
    """Compact catalog slice for the analysis prompt."""
    if not catalog:
        return ""
    lines = ["=== DATA SOURCE CATALOG (allowed indexes / conventions) ==="]
    rule_map = catalog.get("rule_index_map") or {}
    allowed = []
    if rule_id and isinstance(rule_map, dict):
        allowed = rule_map.get(rule_id) or rule_map.get((rule_id or "").strip()) or []
    if allowed:
        lines.append(f"Preferred indexes for rule '{rule_id}': {', '.join(allowed)}")
    for src in (catalog.get("sources") or [])[:8]:
        idx = src.get("index") or ""
        st = src.get("sourcetypes") or []
        notes = (src.get("notes") or "")[:200]
        st_s = ", ".join(st[:4]) if isinstance(st, list) else str(st)
        lines.append(f"- index={idx} sourcetypes=[{st_s}] {notes}".strip())
    conventions = catalog.get("placeholder_conventions") or {}
    if conventions:
        lines.append("Placeholder conventions:")
        for k, v in list(conventions.items())[:6]:
            lines.append(f"  ${k}$: {v}")
    lines.append(
        "Do not invent indexes, sourcetypes, or field names outside this catalog and the supportive SPL templates."
    )
    return "\n".join(lines)


def _ground_phase2_queries(
    phase2_queries: List[Dict[str, Any]],
    supportive_query_defs,
    already_run_titles=None,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    """Map model Phase 2 suggestions onto real supportive playbook templates only.

    - Never emit free-form / invented SPL when a playbook exists or when it does not.
    - Prefer title match to supportive defs; fall back to ranked unused playbook queries.
    - Deprioritize queries that already have saved results for this case.
    """
    already_run_titles = already_run_titles or set()
    title_to_def = {}
    spl_to_def = {}
    for query_def in supportive_query_defs or []:
        if isinstance(query_def, dict):
            title = (query_def.get("title") or "").strip()
            spl_query = (query_def.get("spl_query") or query_def.get("spl") or "").strip()
            description = (query_def.get("description") or "").strip()
        else:
            title = (getattr(query_def, "title", "") or "").strip()
            spl_query = (getattr(query_def, "spl_query", "") or getattr(query_def, "spl", "") or "").strip()
            description = (getattr(query_def, "description", "") or "").strip()
        if not title or not spl_query:
            continue
        payload = {
            "title": title,
            "spl": spl_query,
            "description": description or "Use this query to collect disposition-driving follow-up evidence for the current hypothesis.",
        }
        title_to_def[_normalize_phase2_text(title)] = payload
        spl_to_def[_normalize_phase2_text(spl_query)] = payload

    # No playbook → no Phase 2 SPL cards (do not invent)
    if not title_to_def:
        return []

    grounded: List[Dict[str, Any]] = []
    seen_titles = set()

    for query in phase2_queries or []:
        title = (query.get("title") or "").strip()
        spl_query = (query.get("spl") or "").strip()
        matched = None

        if title:
            matched = title_to_def.get(_normalize_phase2_text(title))
        if not matched and spl_query:
            matched = spl_to_def.get(_normalize_phase2_text(spl_query))
        # Soft title containment match (model shortens/paraphrases titles)
        if not matched and title:
            norm = _normalize_phase2_text(title)
            for key, payload in title_to_def.items():
                if norm and (norm in key or key in norm):
                    matched = payload
                    break

        if not matched:
            # Discard invented SPL; playbook is the only source of truth
            continue

        dedupe_key = _normalize_phase2_text(matched["title"])
        if dedupe_key in seen_titles:
            continue
        # Prefer not-yet-run; still allow if we need to fill later
        grounded.append(dict(matched))
        seen_titles.add(dedupe_key)
        if len(grounded) >= max_queries:
            break

    # Re-order: not-yet-run first. When a later follow-up phase is requested,
    # do not keep resurfacing the same saved cards just because the model
    # repeated their titles; prefer an unused playbook query instead.
    grounded.sort(
        key=lambda q: (0 if _normalize_phase2_text(q.get("title")) not in already_run_titles else 1)
    )
    unused_grounded = [
        q for q in grounded
        if _normalize_phase2_text(q.get("title")) not in already_run_titles
    ]
    if unused_grounded:
        return unused_grounded[:max_queries]

    if grounded and not already_run_titles:
        return grounded[:max_queries]

    # Model suggested nothing usable → ranked fallback from playbook, skip already-run when possible
    return _build_supportive_phase2_fallback(
        supportive_query_defs,
        "",
        "",
        already_run_titles=already_run_titles,
        max_queries=max_queries,
    )


# NOTE: the investigation-state formatting helpers (_format_state_label,
# _format_state_verdict_section, _format_state_closure_section) and the
# grounded Phase 2 section formatter live ONLY in the services layer now:
#   - services/investigation_state.py (state label/verdict/closure sections)
#   - services/analysis_service.py (format_grounded_phase2_section, used by
#     sanitize_analysis_text)
# Earlier copies here had drifted (wording, bold markers) and are deleted.
# Do not reintroduce inline copies — import from the service or extend it.


def _load_supportive_results_for_case(db, case_id: str) -> List[Dict[str, Any]]:
    """Load all saved evidence rows for a case as dicts for investigation-state rebuild."""
    from db.models import SupportiveQueryResult  # type: ignore

    rows = (
        db.query(SupportiveQueryResult)
        .filter(SupportiveQueryResult.case_id == case_id)
        .order_by(SupportiveQueryResult.created_at.desc())
        .all()
    )
    results: List[Dict[str, Any]] = []
    for r in rows:
        try:
            raw = json.loads(r.raw_result) if r.raw_result else None
        except Exception:
            raw = r.raw_result
        results.append({
            "id": r.id,
            "query_title": r.query_title,
            "source_system": r.source_system,
            "raw_result": raw,
            "created_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
        })
    return results


def _rebuild_investigation_state_from_evidence(db, case, analysis_stage: str = "evidence_only"):
    """Rebuild and upsert investigation loop state from current evidence rows."""
    from db.models import InvestigationState  # type: ignore

    supportive_results = _load_supportive_results_for_case(db, case.case_id)
    previous_state_record = db.query(InvestigationState).filter(
        InvestigationState.case_id == case.case_id
    ).first()
    previous_state_payload = (
        _serialize_investigation_state_record(previous_state_record)
        if previous_state_record
        else {}
    )
    analysis_text = (case.analysis_summary or "").strip()
    investigation_state = _build_investigation_state(
        case,
        analysis_text,
        [],
        supportive_results,
        analysis_stage,
        previous_state_payload,
    )
    _upsert_investigation_state(db, InvestigationState, investigation_state)
    return investigation_state


def _purge_case_related_records(
    db,
    case_id: str,
    delete_analysis: bool = True,
    unlink_source_notable: bool = True,
) -> Dict[str, Any]:
    """Remove investigation evidence/state (and optionally analysis + source notable links).

    Used when deleting a triage case so re-paste / re-promote is not blocked by
    leftover rows, and so evidence does not orphan against a missing case.
    """
    from db.models import (  # type: ignore
        AnalysisResult,
        SupportiveQueryResult,
        InvestigationState,
        ClosureNote,
        SplunkEvent,
    )

    stats = {
        "evidence_deleted": 0,
        "analysis_deleted": 0,
        "closure_notes_deleted": 0,
        "investigation_state_deleted": 0,
        "source_notables_unlinked": 0,
        "source_notables_deleted": 0,
    }

    stats["evidence_deleted"] = db.query(SupportiveQueryResult).filter(
        SupportiveQueryResult.case_id == case_id
    ).delete(synchronize_session=False)

    stats["investigation_state_deleted"] = db.query(InvestigationState).filter(
        InvestigationState.case_id == case_id
    ).delete(synchronize_session=False)

    if delete_analysis:
        stats["analysis_deleted"] = db.query(AnalysisResult).filter(
            AnalysisResult.case_id == case_id
        ).delete(synchronize_session=False)

    try:
        stats["closure_notes_deleted"] = db.query(ClosureNote).filter(
            ClosureNote.case_id == case_id
        ).delete(synchronize_session=False)
    except Exception:
        pass

    if unlink_source_notable:
        candidate_events = []
        if case_id.startswith("NOTABLE-"):
            try:
                eid = int(case_id.split("-", 1)[1])
                event = db.query(SplunkEvent).filter(
                    SplunkEvent.id == eid,
                    SplunkEvent.sourcetype == "splunk:notable:pasted",
                ).first()
                if event:
                    candidate_events.append(event)
            except (TypeError, ValueError):
                pass

        if not candidate_events:
            pasted = db.query(SplunkEvent).filter(
                SplunkEvent.sourcetype == "splunk:notable:pasted"
            ).all()
            for event in pasted:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    continue
                if payload.get("promoted_case_id") == case_id:
                    candidate_events.append(event)

        for event in candidate_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                payload = {}

            is_historical = bool(payload.get("historical"))
            if is_historical:
                if payload.get("promoted_case_id") == case_id:
                    payload.pop("promoted_case_id", None)
                    payload.pop("promoted_at", None)
                    event.raw = json.dumps(payload)
                    stats["source_notables_unlinked"] += 1
            else:
                # Open working notable: remove so the same paste can be re-added cleanly.
                db.delete(event)
                stats["source_notables_deleted"] += 1

    return stats


def _evidence_entry_is_valid(entry) -> bool:
    """Decide whether an evidence payload entry is worth persisting.

    Entries with an explicit failure/no-result status are always valid even
    with an empty result body: a legitimate 0-event query or an unavailable
    data source is real execution evidence, not a dropped save. Only a blank
    'success' entry (nothing observed, nothing queried) is skipped.
    """
    if not (getattr(entry, "query_title", "") or "").strip():
        return False
    if (getattr(entry, "result_text", "") or "").strip():
        return True
    if (getattr(entry, "analyst_summary", "") or "").strip():
        return True
    if (getattr(entry, "query_text", "") or "").strip():
        return True
    status = (getattr(entry, "result_status", None) or "success").strip().lower()
    return status not in ("", "success")


def _build_question_driven_followup_queries(
    supportive_query_defs,
    previous_state_payload: Dict[str, Any],
    prompt_supportive_results: List[Dict[str, Any]],
    phase_number: int,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    """Build follow-up cards for later phases that target the CURRENT open
    questions, even after every playbook template has already been run.

    Strategy, in order:
    1. Unused playbook templates (grounded, never run) — preferred.
    2. Fresh variants of already-run templates: clone the closest-matching
       template for each open question, annotate it with the phase number and
       the question it targets, and mark it as a variant so the analyst
       understands it is a re-scoped run (e.g. narrower time window or added
       context), not a replay.

    This guarantees the UI never shows an empty/stale query list while open
    questions remain — the Phase 3+ dead end.
    """
    if phase_number <= 2:
        return []

    already_run_titles = _already_run_supportive_titles(prompt_supportive_results)

    # 1) Prefer genuinely unused playbook templates.
    unused = _build_supportive_phase2_fallback(
        supportive_query_defs,
        "",
        "",
        already_run_titles=already_run_titles,
        max_queries=max_queries,
    )
    unused = [q for q in unused if _normalize_phase2_text(q.get("title")) not in already_run_titles]
    if unused:
        return unused

    # 2) All templates used — build question-targeted variants of the
    #    closest-matching already-run templates.
    questions = [str(q).strip() for q in (previous_state_payload.get("unresolved_questions") or []) if str(q).strip()]
    blockers = [
        str(b).strip()
        for b in (previous_state_payload.get("closure_blockers") or [])
        if str(b).strip() and "remain unresolved" not in str(b).lower()
    ]
    targets = questions + blockers
    if not targets:
        return []

    defs = []
    for query_def in supportive_query_defs or []:
        if isinstance(query_def, dict):
            title = (query_def.get("title") or "").strip()
            spl_query = (query_def.get("spl_query") or query_def.get("spl") or "").strip()
            description = (query_def.get("description") or "").strip()
        else:
            title = (getattr(query_def, "title", "") or "").strip()
            spl_query = (getattr(query_def, "spl_query", "") or "").strip()
            description = (getattr(query_def, "description", "")).strip() if getattr(query_def, "description", None) else ""
        if title and spl_query:
            defs.append({"title": title, "spl": spl_query, "description": description})
    if not defs:
        return []

    variants = []
    for target in targets[:max_queries]:
        target_words = set(re.findall(r"[a-z0-9]+", target.lower()))
        best = None
        best_overlap = -1
        for candidate in defs:
            candidate_text = " ".join([
                candidate["title"], candidate["description"], candidate["spl"],
            ]).lower()
            overlap = len(target_words & set(re.findall(r"[a-z0-9]+", candidate_text)))
            if overlap > best_overlap:
                best_overlap = overlap
                best = candidate
        if not best:
            continue
        short_question = target if len(target) <= 90 else target[:87].rstrip() + "..."
        variants.append({
            "title": f"Phase {phase_number}: {best['title']} (targeted re-check)",
            "spl": best["spl"],
            "description": (
                f"Re-scoped Phase {phase_number} run of '{best['title']}' to resolve the remaining open question: "
                f"\"{short_question}\". Refine the time window or add context before running; the AI will "
                "assess the new result against this question."
            ),
            "target_questions": [target],
            "is_variant": True,
        })
    return variants


def _build_supportive_phase2_fallback(
    supportive_query_defs,
    prior_analysis: str,
    response_text: str,
    already_run_titles=None,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    response_text = response_text or ""
    prior_analysis = prior_analysis or ""
    already_run_titles = already_run_titles or set()
    if not supportive_query_defs:
        return []

    analysis_text = f"{prior_analysis} {response_text}".lower()
    scored_queries = []

    for query_def in supportive_query_defs:
        if isinstance(query_def, dict):
            title = (query_def.get("title") or "").strip()
            spl_query = (query_def.get("spl_query") or query_def.get("spl") or "").strip()
            description = (query_def.get("description") or "").strip()
        else:
            title = (getattr(query_def, "title", "") or "").strip()
            spl_query = (getattr(query_def, "spl_query", "") or "").strip()
            description = (getattr(query_def, "description", "") or "").strip()
        if not title or not spl_query:
            continue

        norm_title = _normalize_phase2_text(title)
        score = 0
        query_text = f"{title} {description} {spl_query}".lower()
        for token in ["host", "user", "process", "parent", "source", "destination", "ip", "timeline", "recent", "auth", "ssh", "root", "outbound", "sysmon", "powershell"]:
            if token in analysis_text and token in query_text:
                score += 2
        if any(token in query_text for token in ["confirm", "validate", "timeline", "recent", "activity"]):
            score += 1
        # Prefer queries not already run with saved results
        if norm_title in already_run_titles:
            score -= 10

        scored_queries.append((score, {
            "title": title,
            "spl": spl_query,
            "description": description or "Use this query to collect disposition-driving follow-up evidence for the current hypothesis.",
        }))

    scored_queries.sort(key=lambda item: item[0], reverse=True)
    # Prefer strictly unused first; if all already run, still return top scored
    unused = [item[1] for item in scored_queries if _normalize_phase2_text(item[1]["title"]) not in already_run_titles]
    if unused:
        return unused[:max_queries]
    return [item[1] for item in scored_queries[:max_queries]]


def _annotate_phase2_targets(phase2_queries, investigation_state):
    """Attach the open question/blocker each grounded query is intended to resolve."""
    state = investigation_state or {}
    questions = [str(item).strip() for item in (state.get("unresolved_questions") or []) if str(item).strip()]
    blockers = [str(item).strip() for item in (state.get("closure_blockers") or []) if str(item).strip()]
    targets = questions + [item for item in blockers if item not in questions and "remain unresolved" not in item.lower()]
    if not targets:
        return phase2_queries
    for query in phase2_queries or []:
        query_text = " ".join([
            str(query.get("title") or ""),
            str(query.get("description") or ""),
            str(query.get("spl") or ""),
        ]).lower()
        ranked = []
        for target in targets:
            words = set(re.findall(r"[a-z0-9]+", target.lower()))
            overlap = sum(1 for word in words if len(word) > 3 and word in query_text)
            ranked.append((overlap, target))
        ranked.sort(key=lambda item: item[0], reverse=True)
        query["target_questions"] = [target for score, target in ranked if score > 0][:2] or targets[:1]
    return phase2_queries


class InvestigationEvidenceEntryPayload(BaseModel):
    query_title: str = Field(..., description="Short title for the investigative query or evidence item")
    query_text: Optional[str] = Field("", description="SPL or other query text used to gather the evidence")
    result_text: Optional[str] = Field("", description="Key rows, findings, or summary pasted by the analyst")
    analyst_summary: Optional[str] = Field("", description="Analyst takeaway or interpretation of the evidence")
    finding_type: Optional[str] = Field("neutral", description="Whether the evidence supports, refutes, or is neutral to the active hypothesis")
    question_resolution: Optional[str] = Field("not_resolved", description="Whether this evidence does not resolve, partially resolves, or resolves a targeted inquiry")
    target_questions: List[str] = Field(default_factory=list, description="Open inquiries targeted by this evidence")
    result_status: Optional[str] = Field("success", description="Execution status: success, no_results, data_source_unavailable, query_failed, not_run, benign_result")
    collection_time: Optional[str] = Field(None, description="ISO timestamp when evidence was collected")
    source_system: Optional[str] = Field("splunk", description="Telemetry source system (splunk, mde, defender, edr, firewall, etc.)")


class InvestigationEvidenceBatchPayload(BaseModel):
    entries: List[InvestigationEvidenceEntryPayload] = Field(default_factory=list)
    source_system: str = Field("phase2_manual", description="Source or stage label for this evidence batch")
    replace_existing: bool = Field(True, description="Replace existing evidence for this case and source_system before saving")


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


class NotableFetchSplRequest(BaseModel):
    """Optional filters used to build a clean notable-fetch SPL query.

    Provide whatever you know (rule name / search_name, host/dest, time
    window). The returned SPL is meant to be run in Splunk, then the
    Statistics table row(s) copied back into the paste box as Label: value
    lines — far more reliable than copying the Incident Review detail pane
    (which glues UI badges into field values).

    Primary path uses the notable index (works when `incident_review` is
    empty for the analyst role). A secondary incident_review variant is
    also returned for environments where that macro is available.
    """

    correlation_search: Optional[str] = Field(
        None, description="ES correlation search / search_name / rule title"
    )
    rule_name: Optional[str] = Field(None, description="Notable rule_name / title")
    dest: Optional[str] = Field(None, description="Destination / host (supports trailing * wildcard)")
    host: Optional[str] = Field(None, description="Host field if different from dest")
    user: Optional[str] = Field(None, description="User / account name")
    event_id: Optional[str] = Field(None, description="Splunk/ES event_id if known")
    rule_id: Optional[str] = Field(None, description="ES rule_id (...@@notable@@...) if known")
    earliest: str = Field("-7d", description="SPL earliest (e.g. -24h, -7d, 09/10/2026:00:00:00)")
    latest: str = Field("now", description="SPL latest")
    max_rows: int = Field(20, ge=1, le=200, description="head N rows")
    notable_index: str = Field(
        "notable",
        description="Index that holds notable events (default: notable). Some sites use risk or a custom index.",
    )


def build_notable_fetch_spl(req: "NotableFetchSplRequest") -> Dict[str, Any]:
    """Build production SPL for open notables with a flawless paste_block.

    Strategy (validated against this ES deployment):
      1. incident_review -> current New/Unassigned/In Progress only
         (dedup rule_id; exclude Resolved/Closed)
      2. Left-join index=notable technical fields by normalized rule name
         + nearest time (rule_id is empty on notable events here)
      3. Emit paste_block = Label: value lines for non-empty fields only
         -> copy one cell into the app paste box (parse_structured_notable)
    """
    earliest = (req.earliest or "-30d").strip() or "-30d"
    latest = (req.latest or "now").strip() or "now"
    max_rows = int(req.max_rows or 50)
    notable_index = (req.notable_index or "notable").strip() or "notable"

    extra_review: List[str] = []
    extra_notable: List[str] = []
    if req.rule_name and str(req.rule_name).strip():
        rn = str(req.rule_name).strip().replace('"', '\\"')
        extra_review.append(f'rule_name="*{rn}*"')
        extra_notable.append(f'search_name="*{rn}*"')
    if req.correlation_search and str(req.correlation_search).strip():
        cs = str(req.correlation_search).strip().replace('"', '\\"')
        extra_review.append(f'rule_name="*{cs}*"')
        extra_notable.append(f'search_name="*{cs}*"')
    if req.dest and str(req.dest).strip():
        d = str(req.dest).strip().replace('"', '\\"')
        if not d.endswith("*"):
            d = d + "*"
        extra_notable.append(f'dest="{d}"')
    if req.host and str(req.host).strip():
        h = str(req.host).strip().replace('"', '\\"')
        if not h.endswith("*"):
            h = h + "*"
        extra_notable.append(f'(host="{h}" OR dest="{h}")')

    review_extra = (" ".join(extra_review)).strip()
    notable_extra = (" ".join(extra_notable)).strip()
    review_extra_clause = f" {review_extra}" if review_extra else ""
    notable_extra_clause = f" {notable_extra}" if notable_extra else ""

    # Note: paste_block uses a real newline inside mvjoin so the cell is multi-line Label: value text.
    nl = "\n"
    spl_parts = [
        "| `incident_review`",
        "| sort 0 - _time",
        "| dedup rule_id",
        "| where (status_label=\"New\" OR status_label=\"Unassigned\" OR status_label=\"In Progress\" OR status=0 OR status=1 OR status=2)",
        "    AND status_label!=\"Resolved\"",
        "    AND status_label!=\"Closed\"",
        "    AND status!=4",
        "    AND status!=5",
        f"| search earliest=\"{earliest}\" latest=\"{latest}\"{review_extra_clause}",
        "| rename _time AS review_time",
        "| eval join_rule=lower(trim(rule_name))",
        "| eval key=1",
        "| join type=left max=0 key",
        f"    [ search (index={notable_index}) earliest=\"{earliest}\" latest=\"{latest}\"{notable_extra_clause}",
        "      | eval join_rule=lower(trim(search_name))",
        r"      | eval join_rule=replace(join_rule, \"^endpoint\\s*-\\s*\", \"\")",
        r"      | eval join_rule=replace(join_rule, \"\\s*-\\s*rule$\", \"\")",
        "      | eval notable_time=_time",
        "      | eval key=1",
        "      | table key, join_rule, notable_time, dest, dest_ip, dest_port, src_ip, src_port,",
        "              user, process, count, first_seen, last_seen, search_name, category, host, description",
        "    ]",
        "| where isnull(search_name) OR lower(trim(rule_name))=join_rule",
        "| eval time_diff=if(isnotnull(notable_time), abs(review_time - notable_time), null())",
        "| eventstats min(time_diff) AS min_diff BY rule_id",
        "| where isnull(time_diff) OR time_diff=min_diff",
        "| sort 0 - review_time",
        f"| head {max_rows}",
        "| eval title_val=if(isnotnull(rule_name) AND rule_name!=\"\", rule_name, search_name)",
        "| eval status_val=if(isnotnull(status_label) AND status_label!=\"\", status_label, \"\")",
        "| eval paste_lines=mvappend(",
        "    if(title_val!=\"\", \"Title: \".title_val, null()),",
        "    if(isnotnull(search_name) AND search_name!=\"\", \"Correlation Search: \".search_name, if(isnotnull(rule_name) AND rule_name!=\"\", \"Correlation Search: \".rule_name, null())),",
        "    if(isnotnull(description) AND description!=\"\", \"Description: \".description, null()),",
        "    if(isnotnull(dest) AND dest!=\"\", \"Destination: \".dest, null()),",
        "    if(isnotnull(dest_ip) AND dest_ip!=\"\", \"Destination IP Address: \".dest_ip, null()),",
        "    if(isnotnull(dest_port) AND dest_port!=\"\", \"Destination Port: \".dest_port, null()),",
        "    if(isnotnull(src_ip) AND src_ip!=\"\", \"Source IP Address: \".src_ip, null()),",
        "    if(isnotnull(src_port) AND src_port!=\"\", \"Source Port: \".src_port, null()),",
        "    if(isnotnull(user) AND user!=\"\", \"User: \".user, null()),",
        "    if(isnotnull(process) AND process!=\"\", \"Process: \".process, null()),",
        "    if(isnotnull(count) AND count!=\"\", \"Count: \".count, null()),",
        "    if(isnotnull(first_seen) AND first_seen!=\"\", \"First Seen: \".first_seen, null()),",
        "    if(isnotnull(last_seen) AND last_seen!=\"\", \"Last Seen: \".last_seen, null()),",
        "    if(isnotnull(category) AND category!=\"\", \"Category: \".mvjoin(category, \" \"), null()),",
        "    if(isnotnull(notable_time), \"Time: \".strftime(notable_time, \"%Y-%m-%dT%H:%M:%S\"), null()),",
        "    if(isnotnull(owner) AND owner!=\"\", \"Owner: \".owner, null()),",
        "    if(status_val!=\"\", \"Status: \".status_val, null()),",
        "    if(isnotnull(urgency) AND urgency!=\"\", \"Urgency: \".urgency, null()),",
        "    if(isnotnull(disposition) AND disposition!=\"\" AND NOT match(disposition, \"^disposition:\\d+$\"), \"Disposition: \".disposition, null()),",
        "    if(isnotnull(rule_id) AND rule_id!=\"\", \"Rule ID: \".rule_id, null()),",
        "    \"Type: notable\"",
        "  )",
        "| eval paste_block=mvjoin(paste_lines, \"\n\")",
        "| table",
        "    review_time, status_label, owner, rule_name, dest, dest_ip, dest_port,",
        "    src_ip, src_port, user, process, count, first_seen, last_seen,",
        "    search_name, category, notable_time, time_diff, paste_block",
    ]
    spl = "\n".join(spl_parts)

    paste_hint_lines = [
        "Flawless paste workflow:",
        "1. Run the SPL in Splunk (Statistics view).",
        "2. Click the paste_block cell for the row you want.",
        "3. Copy (Ctrl+C) — already Label: value lines with blanks omitted.",
        "4. Paste into the app Notable paste box and Save.",
        "5. Do NOT copy from the Incident Review detail pane (UI badges corrupt hostnames).",
        "",
        "Status filter: New / Unassigned / In Progress only (Resolved & Closed excluded).",
        "Owner from incident_review; dest/process/IPs from index=notable.",
    ]

    return {
        "spl": spl,
        "earliest": earliest,
        "latest": latest,
        "max_rows": max_rows,
        "notable_index": notable_index,
        "instructions": paste_hint_lines,
        "why": (
            "IR detail-pane copy glues UI badges into fields (NDC36-81 + badge 0 -> NDC36-810). "
            "This query merges open review state with notable-index technical fields and "
            "emits paste_block (Label: value, non-empty only) for one-click paste into the app."
        ),
    }



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
        print(f"[placeholder_aliases.json sync] Failed to rebuild file: {e}", file=sys.stderr)


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
        print(f"[supportive_rules.json sync] Failed to rebuild file: {e}", file=sys.stderr)

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
        "completed_at": datetime.datetime.utcnow().isoformat()
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
        print(f"[tool_runs] Persistence unavailable: {exc}", file=sys.stderr)


NOTABLE_FIELD_ALIASES = [
    ("Description", "description"),
    ("Additional FieldsValue", "additional_fields_value"),
    ("Additional Fields Value", "additional_fields_value"),
    ("Added Account", "added_account"),
    ("Actor", "actor"),
    ("Coorelation Search", "correlation_search"),
    ("Correlation Search", "correlation_search"),
    ("Search Name", "correlation_search"),
    ("search_name", "correlation_search"),
    ("Security Domain", "security_domain"),
    ("SSL Errors", "ssl_errors"),
    ("Destination", "destination"),
    ("dest", "destination"),
    ("Destination Business Unit", "destination_business_unit"),
    ("Destination Category", "destination_category"),
    ("Destination DNS", "destination_dns"),
    ("Destination Expected", "destination_expected"),
    ("Destination IP Address", "destination_ip"),
    ("dest_ip", "destination_ip"),
    ("Destination NT Hostname", "destination_nt_hostname"),
    ("dest_nt_host", "destination_nt_hostname"),
    ("Destination PCI Domain", "destination_pci_domain"),
    ("Destination Port", "destination_port"),
    ("dest_port", "destination_port"),
    ("Disposition", "disposition"),
    ("Closure Summary", "closure_summary"),
    ("Closure Notes", "closure_summary"),
    ("Closure Note", "closure_summary"),
    ("Username", "username"),
    ("SSH File Path", "ssh_file_path"),
    ("File Path", "file_path"),
    ("File Name", "file_name"),
    ("Process", "process"),
    ("Parent Process", "parent_process"),
    ("Risk Score", "risk_score"),
    ("Severity", "severity"),
    ("Signature", "signature"),
    ("Urgency", "urgency"),
    ("Status", "status"),
    ("Actions", "actions"),
    ("Action", "action"),
    ("Owner", "owner"),
    ("Title", "title"),
    ("Type", "type"),
    ("Time", "time"),
    ("Host", "host"),
    ("Source", "source_ip"),
    ("Source IP Address", "source_ip"),
    ("src_ip", "source_ip"),
    ("Source Port", "source_port"),
    ("src_port", "source_port"),
    ("User Email", "user_email"),
    ("User First Name", "user_first_name"),
    ("User Last Name", "user_last_name"),
    ("User Identity", "user_identity"),
    ("User Category", "user_category"),
    ("User", "user"),
    ("Event ID", "event_id"),
    ("event_id", "event_id"),
    ("Event Hash", "event_hash"),
    ("event_hash", "event_hash"),
    ("Rule ID", "rule_id"),
    ("rule_id", "rule_id"),
    ("Rule Name", "rule_name"),
    ("rule_name", "rule_name"),
    ("Value", "value"),
    # Process / command (CIM + Sysmon-style)
    ("Command Line", "command_line"),
    ("command_line", "command_line"),
    ("CommandLine", "command_line"),
    ("Parent Image", "parent_image"),
    ("parent_image", "parent_image"),
    ("ParentImage", "parent_image"),
    ("Image", "process"),
    ("Process Name", "process"),
    ("process_name", "process"),
    ("Process Path", "process"),
    ("process_path", "process"),
    # Remote / web
    ("Remote URL", "remote_url"),
    ("remoteURL", "remote_url"),
    ("remote_url", "remote_url"),
    ("URL", "remote_url"),
    ("url", "remote_url"),
    # Aggregation / risk extras
    ("Count", "count"),
    ("count", "count"),
    ("First Seen", "first_seen"),
    ("first_seen", "first_seen"),
    ("Last Seen", "last_seen"),
    ("last_seen", "last_seen"),
    ("VPR Score", "vpr_score"),
    ("vpr_score", "vpr_score"),
    ("Risk Tier", "risk_tier"),
    ("risk_tier", "risk_tier"),
    ("Vuln Count", "vuln_count"),
    ("vuln_count", "vuln_count"),
    ("Category", "category"),
    ("category", "category"),
    ("Zero Trust", "zero_trust"),
]

NOTABLE_FIELD_LABELS = {
    "description": "Description",
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
    "added_account": "Added Account",
    "actor": "Actor",
    "value": "Value",
    "ssh_file_path": "SSH File Path",
    "file_path": "File Path",
    "file_name": "File Name",
    "process": "Process",
    "parent_process": "Parent Process",
    "parent_image": "Parent Image",
    "command_line": "Command Line",
    "remote_url": "Remote URL",
    "count": "Count",
    "first_seen": "First Seen",
    "last_seen": "Last Seen",
    "vpr_score": "VPR Score",
    "risk_tier": "Risk Tier",
    "vuln_count": "Vuln Count",
    "category": "Category",
    "zero_trust": "Zero Trust",
    "risk_score": "Risk Score",
    "security_domain": "Security Domain",
    "ssl_errors": "SSL Errors",
    "severity": "Severity",
    "signature": "Signature",
    "event_id": "Event ID",
    "event_hash": "Event Hash",
    "rule_id": "Rule ID",
    "rule_name": "Rule Name",
}

EMBEDDED_FIELD_EXTRACTORS = [
    ("correlation_search", ["Coorelation Search", "Correlation Search"]),
    ("signature", ["Signature"]),
    ("ssl_errors", ["SSL Errors"]),
    ("risk_score", ["Risk Score"]),
]

GLUED_FIELD_TAIL_MARKERS = [
    "Risk Score",
    "SSL Errors",
    "Severity",
    "Urgency",
    "Status",
    "Owner",
    "Disposition",
    "Security Domain",
    "Source Port",
    "Destination Port",
    "Time",
    "Title",
    "Type",
]


def trim_glued_field_tails(value: str, tail_markers: Optional[List[str]] = None) -> str:
    """Trim known field labels that were accidentally glued onto a value."""
    cleaned = (value or "").strip()
    if not cleaned:
        return ""

    markers = tail_markers or GLUED_FIELD_TAIL_MARKERS
    while cleaned:
        lower = cleaned.lower()
        candidate_indexes = []
        for marker in markers:
            idx = lower.find(marker.lower())
            if idx <= 0:
                continue
            prev_char = cleaned[idx - 1]
            if prev_char.isalnum() or prev_char in ")].":
                candidate_indexes.append(idx)
        if not candidate_indexes:
            break
        cleaned = cleaned[:min(candidate_indexes)].strip()

    return cleaned


def is_usable_primary_entity(key: str, value: str) -> bool:
    """Return True when a parsed anchor looks concrete enough for analysis."""
    candidate = (value or "").strip()
    if not candidate:
        return False

    if "[0](http" in candidate or "####" in candidate:
        return False

    if trim_glued_field_tails(candidate) != candidate:
        return False

    if key in {"source_ip", "destination_ip"}:
        return bool(
            re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", candidate)
            or re.fullmatch(r"[A-Fa-f0-9:]+", candidate)
            or re.fullmatch(r"[A-Z0-9_]+", candidate)
        )

    if key in {"host", "destination"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_.:-]+", candidate) or re.fullmatch(r"[A-Z0-9_]+", candidate))

    if key in {"user", "username"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_@.\\:-]+", candidate) or re.fullmatch(r"[A-Z0-9_]+", candidate))

    if key in {"process", "parent_process"}:
        return bool(re.fullmatch(r"[A-Za-z0-9_.:\\/-]+", candidate))

    return True


def parse_pasted_notable(raw_text: str) -> Dict[str, str]:
    """Extract common Splunk notable key/value pairs from pasted text."""
    matches = []
    normalized = normalize_pasted_text(raw_text or "")

    for alias, canonical in sorted(NOTABLE_FIELD_ALIASES, key=lambda item: len(item[0]), reverse=True):
        # For certain aliases like User/Process, only treat them as field
        # labels when they appear at the beginning of a line. This avoids
        # mis-parsing natural-language sentences such as "The suspicious
        # process executed by user has initiated connections to an external
        # IP." where "process"/"user" are ordinary words, not headings.
        alias_lower = alias.lower()
        if canonical in {"user", "process"} and alias_lower in {"user", "process"}:
            pattern = re.compile(r"(?m)^\s*" + re.escape(alias) + r"\b", flags=re.IGNORECASE)
        else:
            pattern = re.compile(re.escape(alias), flags=re.IGNORECASE)

        for match in pattern.finditer(normalized):
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

        # If another known label appears inside the value span (common in
        # glued-together exports like "Owner dalton lewis Security Domain
        # network"), truncate at the earliest such label so each field keeps
        # only its own value instead of swallowing subsequent labels.
        #
        # Use whole-word matching so aliases like "Host" do NOT match inside
        # words like "hosts", which would incorrectly chop values such as
        # "ndc24-1 session hosts west-24 ..." down to just "ndc24-1 session".
        lower_value = value.lower()
        earliest_alias_idx = None
        for alias, _ in NOTABLE_FIELD_ALIASES:
            alias_lower = alias.lower()
            pattern = re.compile(r"\b" + re.escape(alias_lower) + r"\b")
            m = pattern.search(lower_value)
            if not m:
                continue
            idx = m.start()
            if earliest_alias_idx is None or idx < earliest_alias_idx:
                earliest_alias_idx = idx
        if earliest_alias_idx is not None and earliest_alias_idx > 0:
            value = value[:earliest_alias_idx]

        value = re.sub(r"\s+", " ", value).strip()
        if value and match["canonical"] not in parsed:
            parsed[match["canonical"]] = value

    return parsed


def extract_notable_section(raw_text: str, heading: str, end_markers: Optional[List[str]] = None) -> str:
    """Extract a markdown-style section from pasted Incident Review text."""
    if not raw_text:
        return ""

    normalized = raw_text.replace("\r\n", "\n")
    lines = normalized.split("\n")

    def normalize_heading_label(line: str) -> str:
        return re.sub(r"^#+\s*", "", line).strip().lower()

    heading_norm = heading.strip().lower()
    start_index = None
    for index, line in enumerate(lines):
        if normalize_heading_label(line) == heading_norm:
            start_index = index
            break

    if start_index is None or start_index + 1 >= len(lines):
        return ""

    normalized_markers = [m.strip().lower() for m in (end_markers or [])]
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        lower = normalize_heading_label(lines[index])
        if any(lower.startswith(marker) for marker in normalized_markers):
            end_index = index
            break

    return "\n".join(lines[start_index + 1:end_index]).strip()


def infer_notable_fields_from_description(fields: Dict[str, str]) -> Dict[str, str]:
    """Infer a few high-value fields from the freeform description section."""
    description = (fields.get("description") or "").strip()
    if not description:
        return fields

    if not (fields.get("user") or fields.get("username")):
        user_match = re.search(r"\bUser:\s*([^\.\n]+)", description, flags=re.IGNORECASE)
        if user_match:
            fields["username"] = user_match.group(1).strip()

    existing_user = (fields.get("user") or fields.get("username") or "").strip()
    if existing_user and (" experienced " in existing_user.lower() or "observed source ips" in existing_user.lower()):
        acct_match = re.search(r"account\s+([^\s\.]+)", existing_user, flags=re.IGNORECASE)
        if acct_match:
            account_value = acct_match.group(1).strip()
            fields["user"] = account_value
            fields["username"] = account_value

    if not (fields.get("source_ip") or "").strip():
        src_match = re.search(r"Observed\s+Source\s+IPs?:\s*([^\.\s]+)", description, flags=re.IGNORECASE)
        if src_match:
            fields["source_ip"] = src_match.group(1).strip()

    if not fields.get("host"):
        host_match = re.search(r"\bon\s+([^\s\[]+)\s*\[", description, flags=re.IGNORECASE)
        if host_match:
            host_value = host_match.group(1).strip()
            if "$" not in host_value:
                fields["host"] = host_value

    if not fields.get("host"):
        host_match = re.search(r"\bon\s+host\s+([^\s\.]+(?:\.[^\s\.]+)*)", description, flags=re.IGNORECASE)
        if host_match:
            host_value = host_match.group(1).strip().rstrip('.')
            if host_value:
                fields["host"] = host_value

    if not fields.get("destination") and fields.get("host"):
        fields["destination"] = fields["host"]

    if not fields.get("process"):
        child_match = re.search(r"spawned\s+([^\n]+?)\s*\(parent:", description, flags=re.IGNORECASE)
        if child_match:
            child_value = child_match.group(1).strip()
            child_basename = re.split(r"[\\/]", child_value)[-1].strip()
            if child_basename:
                fields["process"] = child_basename

    if not fields.get("parent_process"):
        parent_match = re.search(r"\(parent:\s*([^\)]+)\)", description, flags=re.IGNORECASE)
        if parent_match:
            parent_value = parent_match.group(1).strip()
            parent_basename = re.split(r"[\\/]", parent_value)[-1].strip()
            if parent_basename:
                fields["parent_process"] = parent_basename

    if not fields.get("actor"):
        actor_match = re.search(
            r"The\s+account\s+([^\s\(]+)(?:\s*\([^\)]*\))?\s+added\s+",
            description,
            flags=re.IGNORECASE,
        )
        if actor_match:
            fields["actor"] = actor_match.group(1).strip()

    if not fields.get("added_account"):
        added_match = re.search(
            r"\sadded\s+(.+?)\s+to\s+the\s+administrators\s+group",
            description,
            flags=re.IGNORECASE,
        )
        if added_match:
            fields["added_account"] = added_match.group(1).strip()

    return fields


def normalize_notable_fields(fields: Dict[str, str]) -> Dict[str, str]:
    """Apply small, conservative fix-ups to parsed notable fields."""

    # Current behaviors:
    # - If Destination NT Hostname is present and Destination looks merged or
    #   empty, prefer Destination NT Hostname as the Destination value.
    # - If a field value accidentally captured the "Event Details" section,
    #   trim everything from the first "Event Details" occurrence onward.
    # - If Destination's value still contains "Risk Score" (e.g.,
    #   "Destination NDC56-10Risk Score"), trim at that marker to recover
    #   the hostname.

    # Strip trailing markdown heading artifacts (for example `low ####`) that
    # can appear when a pasted field is immediately followed by a `####`
    # section marker in the source Splunk export.
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        cleaned = re.sub(r"\s+#+\s*$", "", value).strip()
        fields[key] = cleaned

    # Remove markdown link wrappers and obvious inline link tails that often
    # appear in Splunk Incident Review exports, e.g. Host140.18.228.2[0](...)Risk Score
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        cleaned = re.sub(r"\[[^\]]*\]\([^\)]*\)", "", value)
        cleaned = cleaned.replace("(Opens new window)", "")
        fields[key] = re.sub(r"\s+", " ", cleaned).strip()

    # Some exports glue the next field label directly onto the previous
    # field's value. Pull those embedded labels back out conservatively.
    for source_key, value in list(fields.items()):
        if not isinstance(value, str) or not value:
            continue

        current_value = value
        for target_key, aliases in EMBEDDED_FIELD_EXTRACTORS:
            if target_key == source_key:
                continue
            if fields.get(target_key):
                continue

            for alias in aliases:
                pattern = re.compile(rf"\s*{re.escape(alias)}\s*(.+)$", flags=re.IGNORECASE)
                match = pattern.search(current_value)
                if not match:
                    continue

                prefix = current_value[:match.start()].strip()
                suffix = match.group(1).strip()
                if prefix:
                    fields[source_key] = prefix
                if suffix:
                    fields[target_key] = suffix
                current_value = fields[source_key]
                break

    additional_fields = (fields.get("additional_fields_value") or "").strip()
    if additional_fields:
        if not fields.get("actor"):
            actor_match = re.search(r"ActionActor\s*([^\s]+)", additional_fields, flags=re.IGNORECASE)
            if actor_match:
                fields["actor"] = actor_match.group(1).strip()

        if not fields.get("added_account"):
            added_match = re.search(r"Added Account\s*(.+?)(?=\s*Category(?:Other)?\b|\s*Zero Trust\b|$)", additional_fields, flags=re.IGNORECASE)
            if added_match:
                fields["added_account"] = added_match.group(1).strip()

    dest_val = (fields.get("destination") or "").strip()
    if not dest_val or dest_val.lower() in {"business", "category", "dns", "expected", "nt", "pci"}:
        host_from_desc = (fields.get("host") or "").strip()
        if host_from_desc:
            fields["destination"] = host_from_desc

    for key in ["host", "source_ip", "destination", "destination_ip"]:
        entity_value = (fields.get(key) or "").strip()
        if entity_value:
            fields[key] = trim_glued_field_tails(entity_value)

    sev_val = (fields.get("severity") or "").strip()
    if sev_val:
        severity_token = sev_val.split()[0].strip().lower()
        if severity_token in {"low", "medium", "high", "critical"}:
            fields["severity"] = severity_token

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

    # Trim any accidental inclusion of "Event Details" noise from values.
    for key, value in list(fields.items()):
        if not isinstance(value, str):
            continue
        idx = value.find("Event Details")
        if idx != -1:
            cleaned = value[:idx].strip()
            fields[key] = cleaned

    # If destination still contains a concatenated "Risk Score" label,
    # trim it off to recover the hostname.
    dest_val = fields.get("destination") or ""
    rs_idx = dest_val.lower().find("risk score")
    if rs_idx != -1:
        fields["destination"] = dest_val[:rs_idx].strip()

    # Defensive fallback: if Destination/Host looks like a hostname followed by a
    # bare integer (e.g., "NDC45-790 80" or "NDC36-81 0" where the trailing
    # number is a risk score / UI badge), drop the trailing number and keep
    # just the host portion. Applies to space-separated cases only — glued
    # no-space badges (NDC36-810) cannot be disambiguated from legitimate
    # hostnames that end in 0 (NDC45-790) without external inventory, so
    # prefer the | incident_review SPL fetch path for those pastes.
    for host_key in ("destination", "host", "destination_nt_hostname"):
        val = (fields.get(host_key) or "").strip()
        if not val:
            continue
        host_num_match = re.match(r"^([A-Za-z0-9._-]+)\s+\d{1,3}$", val)
        if host_num_match:
            fields[host_key] = host_num_match.group(1)

    # If host/source/destination IP values were masked, keep them if they are
    # still single-token placeholders, but reject obviously merged artifacts.
    for key in ["host", "source_ip", "destination_ip", "destination"]:
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        # Strip residual markdown counters like trailing [0].
        value = re.sub(r"\[\d+\]$", "", value).strip()
        fields[key] = value

    # When the process field contains a long analytic sentence plus an
    # embedded Windows executable path (common in Incident Review exports
    # like "Outbound Connection - Rule ... c:\\windows\\...\\powershell.exe"),
    # prefer the concrete executable path as the process value.
    proc_val = (fields.get("process") or "").strip()
    if proc_val:
        win_path_match = re.search(r"[A-Za-z]:\\\\[^\s]+", proc_val)
        if win_path_match:
            fields["process"] = win_path_match.group(0)

    return fields


def build_generic_enrichment_queries(fields: Dict[str, str]) -> List[Dict[str, str]]:
    """Build a small, safe set of generic SPL queries with concrete values only."""
    queries: List[Dict[str, str]] = []
    correlation_search = (fields.get("correlation_search") or "").strip()
    host = (fields.get("host") or "").strip()
    user = (fields.get("user") or fields.get("username") or "").strip()
    process = (fields.get("process") or "").strip()
    parent_process = (fields.get("parent_process") or "").strip()

    if correlation_search:
        corr_escaped = correlation_search.replace('"', '\\"')
        queries.append({
            "title": "Recent cases for this rule",
            "spl": f'| `incident_review` | search correlation_search="{corr_escaped}" | table _time rule_name correlation_search urgency status owner disposition | sort - _time',
            "description": "Show recent notables for the same correlation search to quickly compare expected versus unusual outcomes.",
        })

    if host:
        host_escaped = host.replace('"', '\\"')
        queries.append({
            "title": "Host activity around alert time",
            "spl": f'search index=* host="{host_escaped}" earliest=-30m latest=+30m | sort 0 _time | table _time host sourcetype source user process Image ParentImage CommandLine',
            "description": "Build a short timeline around the impacted host to see what else was happening nearby.",
        })

    if user:
        user_escaped = user.replace('"', '\\"')
        queries.append({
            "title": "User activity around alert time",
            "spl": f'search index=* earliest=-30m latest=+30m (user="{user_escaped}" OR username="{user_escaped}" OR Account_Name="{user_escaped}") | sort 0 _time | table _time host user sourcetype process Image ParentImage CommandLine',
            "description": "Check what else the same user was doing around the alert window.",
        })

    if process or parent_process:
        clauses = []
        if parent_process:
            clauses.append(f'ParentImage="*\\\\{parent_process}"')
        if process:
            clauses.append(f'Image="*\\\\{process}"')
        if clauses:
            query = 'search index=windows source="XmlWinEventLog:Microsoft-Windows-Sysmon/Operational" EventCode=1 ' + ' '.join(clauses) + ' | table _time ComputerName User ParentImage Image CommandLine ParentCommandLine | sort - _time'
            queries.append({
                "title": "Process lineage validation",
                "spl": query,
                "description": "Validate whether the observed parent and child process relationship is common or suspicious.",
            })

    if not queries:
        queries.append({
            "title": "Recent endpoint process notables",
            "spl": '| `incident_review` | search security_domain=endpoint | table _time correlation_search rule_name urgency status owner disposition | sort - _time | head 25',
            "description": "Start broad: review recent endpoint notables to find the closest comparable activity when the paste is too thin to anchor on a host or user.",
        })

    return queries[:3]


def build_parse_assessment(fields: Dict[str, str], sanitized_text: str, history_text: str) -> Dict[str, Any]:
    """Score how usable a pasted notable is and choose the next workflow mode."""
    score = 0
    missing: List[str] = []

    title = (fields.get("title") or "").strip()
    correlation_search = (fields.get("correlation_search") or "").strip()
    time_value = (fields.get("time") or "").strip()
    primary_entity_keys = ["host", "destination", "destination_ip", "source_ip", "user", "username", "process", "parent_process"]
    identity_context_keys = ["user", "username", "process", "parent_process", "actor", "added_account"]
    clean_primary_entity_keys = [
        key for key in primary_entity_keys if is_usable_primary_entity(key, (fields.get(key) or "").strip())
    ]
    has_primary_entity = bool(clean_primary_entity_keys)
    has_identity_context = any((fields.get(key) or "").strip() for key in identity_context_keys)
    has_context = bool(history_text.strip())
    has_status_bundle = any((fields.get(key) or "").strip() for key in ["disposition", "status", "severity", "urgency"])
    has_detail = bool((fields.get("description") or "").strip() or (sanitized_text or "").strip())
    description = (fields.get("description") or "").strip()
    signature = (fields.get("signature") or "").strip()
    ssl_errors = (fields.get("ssl_errors") or "").strip()
    symptom_text = " ".join([title, correlation_search, description, signature, ssl_errors])
    is_symptom_only_network_case = bool(
        re.search(r"\b(ssl|tls|cipher|handshake|scan(?:ning)?|enumeration|misconfigured client|connection errors?)\b", symptom_text, flags=re.IGNORECASE)
    )

    malformed_keys = []
    for key in ["correlation_search", "host", "destination", "source_ip", "destination_ip", "user", "username", "severity", "urgency", "signature", "time"]:
        value = (fields.get(key) or "").strip()
        if not value:
            continue
        if key in primary_entity_keys:
            if not is_usable_primary_entity(key, value):
                malformed_keys.append(key)
            continue
        if key == "time":
            if re.search(r"\b(HOST|IPV4|USER|REDACTED)_[A-Za-z0-9]+", value):
                malformed_keys.append(key)
            continue
        if any(token in value for token in ["[0](http", "Risk Score", "SSL Errors", "Signature"]) or "####" in value:
            malformed_keys.append(key)

    if correlation_search or (title and title.lower() != "pasted splunk notable"):
        score += 25
    else:
        missing.append("rule identity")

    if time_value:
        score += 20
    else:
        missing.append("time")

    if has_primary_entity:
        score += 20
    else:
        missing.append("primary entity")

    if has_context:
        score += 10
    else:
        missing.append("history or analyst context")

    if has_status_bundle:
        score += 10
    else:
        missing.append("disposition or severity")

    if has_detail:
        score += 15
    else:
        missing.append("supporting detail")

    if malformed_keys:
        score -= min(35, 10 + (5 * len(set(malformed_keys))))
        missing.append("clean field boundaries")

    needs_confirmatory_context = is_symptom_only_network_case and not has_identity_context
    if needs_confirmatory_context:
        score -= 20
        missing.append("confirmatory context")

    generic_title = not title or title.strip().lower() == "pasted splunk notable"
    hard_trigger = generic_title or not time_value or not has_primary_entity or bool(malformed_keys) or needs_confirmatory_context

    if score >= 70 and not hard_trigger:
        mode = "normal"
    elif score >= 40 or correlation_search or title:
        mode = "enrichment"
    else:
        mode = "extraction"

    score = max(0, min(100, score))

    generic_queries = build_generic_enrichment_queries(fields) if mode != "normal" else []

    return {
        "score": score,
        "mode": mode,
        "missing_anchors": missing,
        "generic_queries": generic_queries,
    }


def normalize_pasted_text(raw_text: str) -> str:
    """Normalize paste text so parsers always see real newlines.

    Splunk table-cell copies and some exports often deliver the two-character
    sequences ``\\n`` / ``\\r\\n`` instead of actual line breaks. Without this
    step, line-oriented field parsing collapses and values keep trailing ``\\n``.
    """
    if not raw_text:
        return ""
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n")
    real_newlines = text.count("\n")
    literal_markers = text.count("\\n")
    # Expand escape sequences when the paste is clearly flattened
    if literal_markers > 0 and (real_newlines <= 2 or literal_markers >= real_newlines):
        text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    while "\\\\n" in text:
        text = text.replace("\\\\n", "\n")
    return text


def split_pasted_notables(raw_text: str) -> List[str]:
    """Split a bulk paste that may contain multiple notables into segments.

    Heuristic: treat each line starting with a primary heading ("Title" or
    "Correlation Search") as the beginning of a new notable block. This
    matches the common Splunk Incident Review copy/paste format where each
    notable starts with its own Title/Correlation Search section.
    """
    if not raw_text or not raw_text.strip():
        return []

    normalized = normalize_pasted_text(raw_text)
    lines = normalized.split("\n")

    # Primary heuristic: each card contains a standalone "Notable" line near the top.
    # Use a case-sensitive match so we only pick up the top-of-card
    # "Notable" heading, not the lower-case "notable" line under
    # Event Details.
    notable_pattern = re.compile(r"^\s*Notable\s*$")
    boundaries: List[int] = [
        index for index, line in enumerate(lines) if notable_pattern.match(line)
    ]

    # Fallback heuristic: if we find *no* standalone "Notable" headings at
    # all, we can optionally fall back to Title/Correlation Search labels.
    # However, to avoid over-splitting a single closed Incident Review card
    # into multiple segments (e.g., one for the card and one for a related
    # underlying notable), we only treat this as a multi-notable paste when
    # there are at least three such headings. For one or two headings, keep
    # the entire paste as a single segment.
    if len(boundaries) == 0:
        heading_pattern = re.compile(r"^\s*(Title|Correlation Search)\b", re.IGNORECASE)
        boundaries = [
            index for index, line in enumerate(lines) if heading_pattern.match(line)
        ]
        if len(boundaries) <= 2:
            single = normalized.strip()
            return [single] if single else []

        # Saved single-card artifacts often contain exactly one structured
        # "Correlation Search:" line and one later "Title:" line. That should
        # stay one notable instead of being split into two fragments.
        title_count = sum(1 for line in lines if re.match(r"^\s*Title\b", line, flags=re.IGNORECASE))
        corr_count = sum(1 for line in lines if re.match(r"^\s*(Coorelation Search|Correlation Search)\b", line, flags=re.IGNORECASE))
        if title_count <= 1 and corr_count <= 1:
            single = normalized.strip()
            return [single] if single else []

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

    def normalize_heading_label(line: str) -> str:
        return re.sub(r"^#+\s*", "", line).strip().lower()

    start_index = None
    for index, line in enumerate(lines):
        if normalize_heading_label(line) == "history":
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
        lower = normalize_heading_label(lines[index])
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
    normalized = normalize_pasted_text(raw_text or "")
    for line in normalized.split("\n"):
        match = pattern.match(line)
        if not match:
            continue

        alias = match.group(1).lower()
        # Strip residual escape junk and collapse internal whitespace
        value = match.group(2).replace("\\n", " ").replace("\\t", " ")
        value = re.sub(r"\s+", " ", value).strip()
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


def build_notable_dedup_key(fields: Dict[str, str], sanitized_text: str = "") -> Optional[str]:
    """Build a stable key for deduplicating pasted notables (open or historical).

    Preference order:
      1. Splunk/ES rule_id (unique per notable instance, e.g. ...@@notable@@hash)
      2. event_id / event_hash when present
      3. Composite: correlation/title + time + host/dest + user

    Sanitized text is intentionally excluded so re-pastes with different
    tokenization still match.
    """
    if not fields:
        return None

    rule_id = (fields.get("rule_id") or "").strip()
    if rule_id:
        return f"rule_id:{rule_id}"

    event_id = (fields.get("event_id") or "").strip()
    if event_id:
        return f"event_id:{event_id}"

    event_hash = (fields.get("event_hash") or "").strip()
    if event_hash:
        return f"event_hash:{event_hash}"

    title = (fields.get("title") or fields.get("rule_name") or "").strip()
    corr = (fields.get("correlation_search") or fields.get("rule_name") or "").strip()
    time_val = (fields.get("time") or "").strip()
    host_val = (fields.get("host") or fields.get("destination") or "").strip()
    user_val = (fields.get("user") or fields.get("username") or "").strip()

    anchor = corr or title
    if not anchor:
        return None

    return "|".join([anchor, time_val, host_val, user_val]) or None


# Backwards-compatible alias used by any older call sites
def build_historical_dedup_key(fields: Dict[str, str], sanitized_text: str) -> Optional[str]:
    return build_notable_dedup_key(fields, sanitized_text)


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
    raw_fields = payload.get("raw_fields") or fields
    parse_assessment = payload.get("parse_assessment") or build_parse_assessment(
        raw_fields,
        payload.get("sanitized_text", ""),
        payload.get("history") or "",
    )
    hidden_from_recent = payload.get("hidden_from_recent", False)
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
        "parse_assessment": parse_assessment,
        "raw_fields": raw_fields,
        "fields": fields,
        "sanitized_text": payload.get("sanitized_text", ""),
        "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
        "hidden_from_recent": hidden_from_recent,
    }


# Priority order for compact key-fields shown on collapsed triage cards.
# Only fields that are present and non-empty are included.
TRIAGE_KEY_FIELD_PRIORITY = [
    ("host", "Host"),
    ("destination", "Destination"),
    ("user", "User"),
    ("username", "User"),
    ("ssh_file_path", "SSH File Path"),
    ("file_path", "File Path"),
    ("file_name", "File Name"),
    ("process", "Process"),
    ("parent_process", "Parent Process"),
    ("urgency", "Urgency"),
    ("source_ip", "Source IP"),
    ("destination_ip", "Destination IP"),
    ("destination_port", "Dest Port"),
    ("source_port", "Source Port"),
    ("owner", "Owner"),
    ("severity", "Severity"),
    ("risk_score", "Risk Score"),
]


def _field_lookup(fields: Dict[str, Any], *keys: str) -> str:
    """Return the first non-empty string value for any of the given keys (case-insensitive)."""
    if not fields:
        return ""
    # Direct hits first
    for key in keys:
        val = fields.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    # Case-insensitive fallback
    lower_map = {str(k).lower(): v for k, v in fields.items()}
    for key in keys:
        val = lower_map.get(key.lower())
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def extract_triage_key_fields(fields: Dict[str, Any], raw_fields: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Build a compact ordered dict of high-value fields for triage card previews.

    Pulls from canonical parsed fields first, then raw_fields as a fallback so
    rule-specific values (SSH File Path, process, etc.) surface even when the
    original paste used slightly different labels.
    """
    merged: Dict[str, Any] = {}
    if raw_fields and isinstance(raw_fields, dict):
        merged.update(raw_fields)
    if fields and isinstance(fields, dict):
        merged.update(fields)

    result: Dict[str, str] = {}
    seen_labels: set = set()
    host_val = _field_lookup(merged, "host", "Host")
    dest_val = _field_lookup(merged, "destination", "Destination", "dest")

    for key, label in TRIAGE_KEY_FIELD_PRIORITY:
        if label in seen_labels:
            continue
        value = _field_lookup(merged, key)
        if not value:
            continue
        # Skip Destination when it is identical to Host (common on endpoint notables)
        if label == "Destination" and host_val and value.lower() == host_val.lower():
            continue
        # Skip Username duplicate when User already present
        if label == "User" and "User" in seen_labels:
            continue
        result[label] = value
        seen_labels.add(label)
    return result


def build_triage_analysis_summary(
    title: str,
    fields: Dict[str, Any],
    notable_time: Optional[str] = None,
) -> str:
    """Build a richer analysis_summary string for newly promoted triage cases."""
    summary_parts = [title] if title else []
    disposition = _field_lookup(fields, "disposition")
    if disposition:
        summary_parts.append(f"Disposition: {disposition}")
    status = _field_lookup(fields, "status")
    if status:
        summary_parts.append(f"Status: {status}")
    if notable_time:
        summary_parts.append(f"Time: {notable_time}")
    host = _field_lookup(fields, "host")
    dest = _field_lookup(fields, "destination", "dest")
    if host:
        summary_parts.append(f"Host: {host}")
    if dest and (not host or dest.lower() != host.lower()):
        summary_parts.append(f"Destination: {dest}")
    user = _field_lookup(fields, "user", "username")
    if user:
        summary_parts.append(f"User: {user}")
    urgency = _field_lookup(fields, "urgency")
    if urgency:
        summary_parts.append(f"Urgency: {urgency}")
    path = _field_lookup(fields, "ssh_file_path", "file_path", "file_name")
    if path:
        label = "SSH File Path" if fields.get("ssh_file_path") else ("File Path" if fields.get("file_path") else "File Name")
        # Prefer explicit label when available
        if _field_lookup(fields, "ssh_file_path"):
            label = "SSH File Path"
        elif _field_lookup(fields, "file_path"):
            label = "File Path"
        else:
            label = "File Name"
        summary_parts.append(f"{label}: {path}")
    process = _field_lookup(fields, "process")
    if process:
        summary_parts.append(f"Process: {process}")
    parent = _field_lookup(fields, "parent_process")
    if parent:
        summary_parts.append(f"Parent Process: {parent}")
    return " | ".join(summary_parts)


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
        # Pasted notables usually arrive without an ES disposition field. A
        # neutral 0.5 baseline plus the loop's +0.20 evidence bonus cap can
        # never reach the 0.80 closure gate, which made organic closure
        # impossible for promoted pastes. Start undetermined pastes exactly
        # at the gate so earned evidence closes them naturally, while cases
        # with refuting or missing evidence stay gated below it.
        return 0.8
    if "false positive" in normalized or "true positive" in normalized or "benign" in normalized:
        return 0.8
    return 0.6


def _resolve_correlation_rule(db, correlation_model, anchor_text: str):
    anchor = _normalize_rule_match_text(anchor_text)
    if not anchor:
        return None

    rules = db.query(correlation_model).filter(correlation_model.enabled == 1).all()

    for rule in rules:
        name = _normalize_rule_match_text(rule.rule_name or "")
        if name and name == anchor:
            return rule

    for rule in rules:
        name = _normalize_rule_match_text(rule.rule_name or "")
        if not name:
            continue
        if anchor in name or name in anchor:
            return rule

    anchor_tokens = {
        token for token in anchor.split()
        if token not in {"endpoint", "network", "rule", "alert", "detection"}
    }
    if not anchor_tokens:
        return None

    best_rule = None
    best_score = 0
    for rule in rules:
        name_tokens = {
            token for token in _normalize_rule_match_text(rule.rule_name or "").split()
            if token not in {"endpoint", "network", "rule", "alert", "detection"}
        }
        if not name_tokens:
            continue

        overlap = anchor_tokens & name_tokens
        if not overlap:
            continue

        score = len(overlap)
        if anchor_tokens.issubset(name_tokens):
            score += 10

        if score > best_score:
            best_rule = rule
            best_score = score

    return best_rule

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
        "created_at": datetime.datetime.utcnow().isoformat(),
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
            print(f"[tool_runs] Could not load persisted job: {exc}", file=sys.stderr)
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
                age_seconds = (datetime.datetime.utcnow() - row.created_at).total_seconds()
                if age_seconds > STALE_JOB_SECONDS:
                    row_status = JobStatus.FAILED.value
                    row_stderr = (row_stderr or "") + ("\n" if row_stderr else "") + "Job did not report completion and was marked interrupted after 10 minutes."
                    try:
                        from db.models import SessionLocal as _SessionLocal
                        cleanup_db = _SessionLocal()
                        row.status = row_status
                        row.stderr = row_stderr
                        row.completed_at = datetime.datetime.utcnow()
                        cleanup_db.merge(row)
                        cleanup_db.commit()
                        cleanup_db.close()
                    except Exception as exc:
                        print(f"[tool_runs] Could not mark stale job: {exc}", file=sys.stderr)
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
        print(f"[tool_runs] Could not load persisted jobs: {exc}", file=sys.stderr)
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
        print(f"[tool_runs] Could not delete persisted job: {exc}", file=sys.stderr)
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
        print(f"[tool_runs] Could not clear persisted jobs: {exc}", file=sys.stderr)
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
                _purge_case_related_records(
                    db,
                    delete_case_id,
                    delete_analysis=delete_analysis,
                    unlink_source_notable=True,
                )
                db.delete(case)
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

        # Batch-load source pasted notables so collapsed cards can show key fields
        # (Host, User, Path, etc.) without requiring an expand/click.
        case_ids = [r.case_id for r in results]
        notable_by_case: Dict[str, Dict[str, Any]] = {}
        if case_ids:
            try:
                from db.models import SplunkEvent
                pasted = db.query(SplunkEvent).filter(
                    SplunkEvent.sourcetype == "splunk:notable:pasted"
                ).order_by(SplunkEvent.ingested_at.desc()).all()
                for event in pasted:
                    try:
                        payload = json.loads(event.raw) if event.raw else {}
                    except Exception:
                        continue
                    promoted = payload.get("promoted_case_id")
                    if promoted and promoted in case_ids and promoted not in notable_by_case:
                        fields = payload.get("fields") or {}
                        raw_fields = payload.get("raw_fields") or fields
                        notable_by_case[promoted] = {
                            "fields": fields,
                            "raw_fields": raw_fields,
                            "key_fields": extract_triage_key_fields(fields, raw_fields),
                        }
            except Exception:
                # Non-fatal: cards still render without key_fields
                pass

        return [
            {
                "case_id": r.case_id,
                "rule_name": r.rule_name,
                "rule_id": r.rule_id,
                "verdict": r.verdict,
                "confidence_score": r.confidence_score,
                "analysis_summary": r.analysis_summary,
                "remediation_steps": r.remediation_steps,
                "triaged_at": r.triaged_at.isoformat() if r.triaged_at else None,
                "from_pasted_notable": r.case_id in notable_by_case,
                "key_fields": (notable_by_case.get(r.case_id) or {}).get("key_fields") or {},
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


def _enrich_timeline_with_evidence_ids(db, case_id: str, state_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach SupportiveQueryResult ids onto timeline items so the UI can delete them.

    Older investigation_state rows were saved before timeline items included
    ``id``. Without this enrichment, the Delete button stays hidden forever
    for those cases until evidence is re-saved.
    """
    if not state_payload:
        return state_payload

    evidence_summary = state_payload.get("evidence_summary") or {}
    timeline = evidence_summary.get("timeline") or []
    if not timeline:
        return state_payload

    needs_ids = any(not item.get("id") for item in timeline if isinstance(item, dict))
    if not needs_ids:
        return state_payload

    try:
        from db.models import SupportiveQueryResult  # type: ignore

        rows = (
            db.query(SupportiveQueryResult)
            .filter(SupportiveQueryResult.case_id == case_id)
            .order_by(SupportiveQueryResult.created_at.desc())
            .all()
        )
    except Exception:
        return state_payload

    # Map (title, source_system) -> list of ids (newest first)
    by_key: Dict[tuple, list] = {}
    for r in rows:
        key = ((r.query_title or "").strip().lower(), (r.source_system or "").strip().lower())
        by_key.setdefault(key, []).append(r.id)

    for item in timeline:
        if not isinstance(item, dict) or item.get("id"):
            continue
        key = (
            (item.get("title") or "").strip().lower(),
            (item.get("source_system") or "").strip().lower(),
        )
        ids = by_key.get(key) or []
        if ids:
            item["id"] = ids.pop(0)

    evidence_summary["timeline"] = timeline
    state_payload["evidence_summary"] = evidence_summary
    return state_payload


@app.get("/api/db/triage/{case_id}/investigation-state", tags=["Database"])
def get_investigation_state(case_id: str):
    """Return the latest persisted investigation loop state for a case."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, InvestigationState

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        state = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        if state:
            payload = _serialize_investigation_state_record(state)
            return _enrich_timeline_with_evidence_ids(db, case_id, payload)

        return {
            "case_id": case.case_id,
            "rule_id": case.rule_id or "",
            "current_hypothesis": (case.analysis_summary or "").strip(),
            "provisional_disposition": (case.verdict or "undetermined").strip().lower(),
            "disposition_confidence": float(case.confidence_score or 0.0),
            "loop_status": "collecting_evidence",
            "iteration_count": 0,
            "unresolved_questions": [],
            "closure_blockers": ["No persisted investigation state yet. Run analysis to initialize the loop."],
            "recommended_next_actions": [],
            "evidence_summary": {
                "total_items": 0,
                "by_finding": {"supports": 0, "refutes": 0, "neutral": 0},
                "by_source_system": {},
                "recent_titles": [],
            },
            "last_analysis_stage": "initial",
            "updated_at": None,
        }
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@app.get("/api/db/triage/{case_id}/evidence", tags=["Database"])
def list_case_evidence(
    case_id: str,
    source_system: Optional[str] = Query(default=None, description="Optional source/stage filter, e.g. phase2_manual"),
):
    """List saved investigation evidence for a case.

    This uses the existing supportive_query_results table as a durable
    evidence ledger so the analyst's findings can be replayed into future
    analyses without relying on browser-local state.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SupportiveQueryResult

        db = SessionLocal()
        query = db.query(SupportiveQueryResult).filter(SupportiveQueryResult.case_id == case_id)
        if source_system:
            query = query.filter(SupportiveQueryResult.source_system == source_system)

        rows = query.order_by(SupportiveQueryResult.created_at.asc()).all()
        items = []
        for row in rows:
            try:
                raw_result = json.loads(row.raw_result) if row.raw_result else {}
            except Exception:
                raw_result = {"result_text": row.raw_result}

            items.append({
                "id": row.id,
                "case_id": row.case_id,
                "rule_id": row.rule_id,
                "query_title": row.query_title,
                "source_system": row.source_system,
                "raw_result": raw_result,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })

        return items
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@app.post("/api/db/triage/{case_id}/evidence", tags=["Database"])
def save_case_evidence(case_id: str, payload: InvestigationEvidenceBatchPayload):
    """Persist a batch of case-linked investigation evidence.

    The UI uses this for Phase 2 analyst-pasted SPL results so the AI can
    reason over durable evidence on subsequent analyses rather than only the
    current browser prompt state.
    """
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from sqlalchemy import func  # type: ignore
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult, InvestigationState

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        source_system = (payload.source_system or "phase2_manual").strip() or "phase2_manual"
        valid_entries = [e for e in (payload.entries or []) if _evidence_entry_is_valid(e)]
        if payload.replace_existing and valid_entries:
            db.query(SupportiveQueryResult).filter(
                SupportiveQueryResult.case_id == case_id,
                SupportiveQueryResult.source_system == source_system,
            ).delete()

        try:
            max_id = db.query(func.max(SupportiveQueryResult.id)).scalar() or 0
        except Exception:
            max_id = 0
        next_id = int(max_id) + 1

        saved_count = 0
        for entry in payload.entries:
            title = (entry.query_title or "").strip()
            result_text = (entry.result_text or "").strip()
            analyst_summary = (entry.analyst_summary or "").strip()
            query_text = (entry.query_text or "").strip()
            result_status = (getattr(entry, "result_status", None) or "success").strip().lower()
            # Legacy 'benign_result' was an analyst verdict; persist it as a
            # plain success so direction is derived from AI analysis instead.
            if result_status == "benign_result":
                result_status = "success"

            if not title:
                continue
            if not (result_text or analyst_summary or query_text) and result_status in ("", "success"):
                continue

            raw_result = json.dumps(
                {
                    "query_text": query_text,
                    "result_text": result_text,
                    "analyst_summary": analyst_summary,
                    "finding_type": (entry.finding_type or "neutral").strip() or "neutral",
                    "question_resolution": (entry.question_resolution or "not_resolved").strip() or "not_resolved",
                    "target_questions": [str(q).strip() for q in (entry.target_questions or []) if str(q).strip()],
                    "result_status": result_status,
                    "collection_time": getattr(entry, "collection_time", None) or datetime.datetime.utcnow().isoformat(),
                    "source_system": getattr(entry, "source_system", source_system) or source_system,
                },
                ensure_ascii=False,
            )

            db.add(
                SupportiveQueryResult(
                    id=next_id,
                    case_id=case_id,
                    rule_id=case.rule_id or "",
                    query_title=title,
                    source_system=source_system,
                    raw_result=raw_result,
                )
            )
            next_id += 1
            saved_count += 1

        # Session uses autoflush=False, so newly added rows are invisible to
        # subsequent queries until we flush. Without this, the investigation
        # state rebuild runs against stale data (missing the just-saved
        # evidence) and the Evidence Timeline does not update until a later
        # analysis request re-reads after commit.
        db.flush()

        # Rebuild investigation loop state from the latest evidence so the
        # Investigation Loop view reflects saved entries even when the
        # analyst has not rerun AI analysis yet.
        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )

        db.commit()
        return {
            "success": True,
            "saved_count": saved_count,
            "source_system": source_system,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass



@app.delete("/api/db/triage/{case_id}/evidence/{evidence_id}", tags=["Database"])
def delete_case_evidence(case_id: str, evidence_id: int):
    """Delete a single saved investigation evidence item and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        row = db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.id == evidence_id,
            SupportiveQueryResult.case_id == case_id,
        ).first()
        if not row:
            raise HTTPException(
                status_code=404,
                detail=f"Evidence {evidence_id} not found for case {case_id}",
            )

        deleted_title = row.query_title
        deleted_source = row.source_system
        db.delete(row)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_id": evidence_id,
            "query_title": deleted_title,
            "source_system": deleted_source,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@app.post("/api/db/triage/{case_id}/evidence/{evidence_id}/delete", tags=["Database"])
def delete_case_evidence_post(case_id: str, evidence_id: int):
    """POST wrapper for environments that disallow DELETE from the browser UI."""
    return delete_case_evidence(case_id=case_id, evidence_id=evidence_id)


@app.post("/api/db/triage/{case_id}/evidence/batch-delete", tags=["Database"])
def delete_case_evidence_batch(case_id: str, payload: dict):
    """Delete multiple saved investigation evidence items by ID and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        raw_ids = payload.get("ids") or payload.get("evidence_ids") or []
        ids = [int(i) for i in raw_ids if i is not None and str(i).isdigit()]
        if not ids:
            return {"success": True, "deleted_ids": []}

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id,
            SupportiveQueryResult.id.in_(ids)
        ).delete(synchronize_session=False)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_ids": ids,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass


@app.post("/api/db/triage/{case_id}/evidence/delete-all", tags=["Database"])
def delete_all_case_evidence(case_id: str):
    """Delete all saved investigation evidence for a case and reset loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        db.query(SupportiveQueryResult).filter(
            SupportiveQueryResult.case_id == case_id
        ).delete(synchronize_session=False)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_all": True,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
    except Exception as e:
        if db is not None:
            db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
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

        purge_stats = _purge_case_related_records(
            db,
            case_id,
            delete_analysis=delete_analysis,
            unlink_source_notable=True,
        )
        db.delete(case)
        db.commit()

        return {"success": True, "purged": purge_stats}
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


@app.post("/api/db/triage/batch-delete", tags=["Database"])
def batch_delete_triage_cases(payload: Dict[str, Any]):
    """Delete multiple triage cases in one call.

    Expects JSON payload:
    {"case_ids": ["CASE-1", "CASE-2", ...], "delete_analysis": true/false}
    """
    try:
        case_ids = payload.get("case_ids") or []
        delete_analysis = bool(payload.get("delete_analysis"))

        if not isinstance(case_ids, list) or not case_ids:
            raise HTTPException(status_code=400, detail="case_ids list is required")

        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, AnalysisResult

        db = SessionLocal()
        deleted: List[str] = []
        missing: List[str] = []

        try:
            for case_id in case_ids:
                cid = (case_id or "").strip()
                if not cid:
                    continue
                case = db.query(TriageResult).filter(TriageResult.case_id == cid).first()
                if not case:
                    missing.append(cid)
                    continue
                _purge_case_related_records(
                    db,
                    cid,
                    delete_analysis=delete_analysis,
                    unlink_source_notable=True,
                )
                db.delete(case)
                deleted.append(cid)

            db.commit()
        finally:
            db.close()

        return {"success": True, "deleted": deleted, "missing": missing}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/db/notables/generate-fetch-spl", tags=["Database"])
def generate_notable_fetch_spl(request: NotableFetchSplRequest):
    """Generate SPL to pull clean notable fields from Splunk.

    Primary query uses the notable index (works when `incident_review` is
    empty for the analyst role). Response also includes spl_incident_review
    as a secondary option.

    Prefer this over copying the Incident Review detail pane — UI badges
    (red count chips, risk scores) frequently get glued into hostnames
    (e.g. NDC36-81 + badge 0 → NDC36-810).

    Workflow:
      1. Call this endpoint with whatever identifiers you know.
      2. Run the returned `spl` in Splunk.
      3. Copy the Statistics row as Label: value lines.
      4. Paste into the normal notable paste box.
    """
    try:
        return build_notable_fetch_spl(request)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/db/notables/generate-fetch-spl", tags=["Database"])
def generate_notable_fetch_spl_get(
    correlation_search: Optional[str] = Query(None),
    rule_name: Optional[str] = Query(None),
    dest: Optional[str] = Query(None),
    host: Optional[str] = Query(None),
    user: Optional[str] = Query(None),
    event_id: Optional[str] = Query(None),
    rule_id: Optional[str] = Query(None),
    earliest: str = Query("-7d"),
    latest: str = Query("now"),
    max_rows: int = Query(20, ge=1, le=200),
    notable_index: str = Query("notable"),
):
    """GET convenience wrapper for generate_notable_fetch_spl."""
    req = NotableFetchSplRequest(
        correlation_search=correlation_search,
        rule_name=rule_name,
        dest=dest,
        host=host,
        user=user,
        event_id=event_id,
        rule_id=rule_id,
        earliest=earliest,
        latest=latest,
        max_rows=max_rows,
        notable_index=notable_index,
    )
    return generate_notable_fetch_spl(req)


@app.post("/api/db/notables/paste", tags=["Database"])
def paste_notable(request: PastedNotableRequest):
    """Parse, sanitize, and store a pasted Splunk notable in the database."""
    raw_text = normalize_pasted_text(request.raw_text or "").strip()
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
        added_count = 0
        skipped_count = 0
        skipped_segments: List[str] = []
        pending_events: List[Any] = []

        # Always load existing pasted notables for dedup (open + historical).
        # Prefer rule_id / event_id / event_hash; fall back to composite key.
        existing_by_key: Dict[str, Any] = {}
        existing_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

        # Preload existing triage case ids so orphaned promotions do not block re-paste.
        from db.models import TriageResult as _TriageResultForDedup  # type: ignore
        live_case_ids = {
            row.case_id
            for row in db.query(_TriageResultForDedup.case_id).all()
        }

        for event in existing_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            # Soft-deleted / hidden notables should not block a fresh paste.
            if payload.get("hidden_from_recent"):
                continue

            fields = payload.get("raw_fields") or payload.get("fields", {}) or {}
            key = build_notable_dedup_key(fields, payload.get("sanitized_text", ""))
            if key and key not in existing_by_key:
                existing_by_key[key] = {
                    "event": event,
                    "payload": payload,
                    "fields": fields,
                    "artifact_paths": payload.get("artifact_paths") or {},
                    "promoted_case_id": payload.get("promoted_case_id"),
                }

        for index, segment_text in enumerate(segments):
            parsed_fields = parse_structured_notable(segment_text)
            if not parsed_fields:
                parsed_fields = parse_pasted_notable(segment_text)

            # Apply small normalization tweaks (e.g., Destination from
            # Destination NT Hostname) before rendering/sanitizing.
            parsed_fields = normalize_notable_fields(parsed_fields)
            description_text = extract_notable_section(
                segment_text,
                "Description",
                [
                    "event details",
                    "correlation search",
                    "history",
                    "related investigations",
                    "drill-down search",
                    "adaptive responses",
                ],
            )
            if description_text and not parsed_fields.get("description"):
                parsed_fields["description"] = description_text
            parsed_fields = infer_notable_fields_from_description(parsed_fields)
            raw_query_fields = normalize_notable_fields(parsed_fields.copy())

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
                parsed_sanitized_fallback = parse_pasted_notable(sanitized_text)
                for key, value in parsed_sanitized_fallback.items():
                    if value and key not in sanitized_fields:
                        sanitized_fields[key] = value
                # preserve original time / rule_id if we parsed them before masking
                if parsed_fields.get("time"):
                    sanitized_fields["time"] = parsed_fields["time"]
                if parsed_fields.get("rule_id") and not sanitized_fields.get("rule_id"):
                    sanitized_fields["rule_id"] = parsed_fields["rule_id"]
                sanitized_fields = normalize_notable_fields(sanitized_fields)
            else:
                # no masking: keep parsed fields and structured text as-is
                sanitized_text = structured_text
                mapping = {}
                sanitized_fields = normalize_notable_fields(parsed_fields.copy())

            sanitized_history = extract_notable_history(sanitized_text)
            if not sanitized_history:
                sanitized_history = (sanitized_fields.get("closure_summary") or "").strip()
            parse_assessment = build_parse_assessment(raw_query_fields, sanitized_text, sanitized_history)

            # Dedup for both open and historical pastes.
            artifact_paths = None
            event = None
            dedup_key = build_notable_dedup_key(sanitized_fields, sanitized_text)
            # Also try raw (pre-redaction) fields so rule_id is never lost to tokens
            if not dedup_key:
                dedup_key = build_notable_dedup_key(raw_query_fields, sanitized_text)
            existing = existing_by_key.get(dedup_key) if dedup_key else None
            was_deduplicated = False
            skip_reason = None

            if existing:
                promoted_case_id = existing.get("promoted_case_id")
                # If the prior paste was promoted but that triage case was deleted,
                # treat this as free to re-add (update the existing event in place).
                orphaned_promotion = bool(
                    promoted_case_id and promoted_case_id not in live_case_ids
                )
                if not orphaned_promotion:
                    event = existing["event"]
                    artifact_paths = existing.get("artifact_paths") or {}
                    was_deduplicated = True
                    skipped_count += 1
                    seg_num = index + 1
                    existing_id = event.id if event else None
                    key_preview = (dedup_key or "")[:48]
                    skip_reason = f"already exists as event {existing_id} ({key_preview})"
                    skipped_segments.append(f"#{seg_num}")
                else:
                    # Reclaim the orphaned event: fall through to update path below
                    # by removing it from the dedup map and deleting the stale row.
                    stale_event = existing.get("event")
                    if stale_event is not None:
                        try:
                            db.delete(stale_event)
                            db.flush()
                        except Exception:
                            pass
                    if dedup_key in existing_by_key:
                        del existing_by_key[dedup_key]
                    existing = None

            if existing:
                pass  # already handled as skip above
            else:
                artifact_paths = save_notable_artifacts(
                    get_platform_root(), sanitized_text, mapping, sanitized_fields
                )
                payload = {
                    "record_type": "splunk_notable_paste",
                    "raw_fields": raw_query_fields,
                    "fields": sanitized_fields,
                    "sanitized_text": sanitized_text,
                    "history": sanitized_history,
                    "parse_assessment": parse_assessment,
                    "saved_at": datetime.datetime.utcnow().isoformat(),
                    "artifact_paths": artifact_paths,
                    "historical": request.historical,
                    "segment_index": index,
                    "segment_count": len(segments),
                    "dedup_key": dedup_key,
                }

                source = (
                    sanitized_fields.get("correlation_search")
                    or sanitized_fields.get("title")
                    or sanitized_fields.get("rule_name")
                    or "Pasted Splunk notable"
                )
                host = (
                    sanitized_fields.get("host")
                    or sanitized_fields.get("destination")
                    or "unknown"
                )
                timestamp = parse_notable_timestamp(parsed_fields.get("time", ""))

                event = SplunkEvent(
                    sourcetype="splunk:notable:pasted",
                    source=source,
                    host=host,
                    raw=json.dumps(payload),
                    timestamp=timestamp,
                )
                db.add(event)
                pending_events.append(event)
                added_count += 1

                # Cache so later segments in the same paste also dedup.
                # event.id is assigned on flush/commit; use a placeholder
                # until the single batch commit below.
                if dedup_key:
                    existing_by_key[dedup_key] = {
                        "event": event,
                        "payload": payload,
                        "fields": sanitized_fields,
                        "artifact_paths": artifact_paths,
                    }

            events_info.append({
                "event_id": None,  # filled after batch commit
                "event_ref": event,
                "raw_fields": raw_query_fields,
                "parsed_fields": sanitized_fields,
                "parse_assessment": parse_assessment,
                "artifact_paths": artifact_paths or {},
                "mapping_entries": len(mapping),
                "deduplicated": was_deduplicated,
                "skip_reason": skip_reason,
                "segment_index": index + 1,
            })
            total_mapping_entries += len(mapping)

        # Single commit for the whole bulk paste (major speed win vs per-row commits)
        if pending_events:
            db.commit()
            for event in pending_events:
                try:
                    db.refresh(event)
                except Exception:
                    pass

        for info in events_info:
            ref = info.pop("event_ref", None)
            if ref is not None:
                info["event_id"] = getattr(ref, "id", None)

        first_event = events_info[0] if events_info else {}

        # Human-readable summary, e.g.
        # "Added 4 notables. Skipped #2 and #5 (already in database)."
        if skipped_count and added_count:
            message = (
                f"Added {added_count} notable{'s' if added_count != 1 else ''}. "
                f"Skipped {', '.join(skipped_segments)} "
                f"(already in database)."
            )
        elif skipped_count and not added_count:
            message = (
                f"No new notables added. Skipped {', '.join(skipped_segments)} "
                f"(already in database)."
            )
        elif added_count:
            message = f"Added {added_count} notable{'s' if added_count != 1 else ''}."
        else:
            message = "No notables processed."

        return {
            "success": True,
            "segment_count": len(segments),
            "added": added_count,
            "skipped": skipped_count,
            "message": message,
            "events": events_info,
            # Backwards-compatible single-event fields (use first segment)
            "event_id": first_event.get("event_id"),
            "raw_fields": first_event.get("raw_fields"),
            "parsed_fields": first_event.get("parsed_fields"),
            "parse_assessment": first_event.get("parse_assessment"),
            "artifact_paths": first_event.get("artifact_paths") or {},
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
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()

        # Optional delete/hide step using the same session. Historical
        # notables or those backing an existing triage case are retained
        # and hidden from the recent list instead of being deleted.
        if delete_event_id is not None:
            event = db.query(SplunkEvent).filter(
                SplunkEvent.id == delete_event_id,
                SplunkEvent.sourcetype == "splunk:notable:pasted",
            ).first()
            if event:
                try:
                    payload = json.loads(event.raw) if event.raw else {}
                except Exception:
                    payload = {}

                is_historical = bool(payload.get("historical"))

                promoted_case_id = payload.get("promoted_case_id")
                has_triage_case = False
                try:
                    if promoted_case_id:
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                        has_triage_case = existing_case is not None
                    else:
                        canonical_case_id = f"NOTABLE-{event.id}"
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                        has_triage_case = existing_case is not None
                except Exception:
                    has_triage_case = False

                if is_historical or has_triage_case:
                    payload["hidden_from_recent"] = True
                    event.raw = json.dumps(payload)
                else:
                    db.delete(event)
                db.commit()
        rows = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).limit(limit).all()

        serialized = [serialize_recent_notable(row) for row in rows]
        visible = [n for n in serialized if not n.get("hidden_from_recent")]
        return visible
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.get("/api/db/notables/historical", tags=["Database"])
def list_historical_notables(
    limit: int = Query(20, ge=1, le=500),
):
    """List a high-level summary of historical (closed) pasted notables.

    Returns a compact view suitable for quick baseline reference without
    flooding the main triage grid.
    """
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent

        db = SessionLocal()
        rows = (
            db.query(SplunkEvent)
            .filter(SplunkEvent.sourcetype == "splunk:notable:pasted")
            .order_by(SplunkEvent.ingested_at.desc())
            .limit(limit)
            .all()
        )

        summaries = []
        for event in rows:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue

            if not payload.get("historical"):
                continue

            fields = payload.get("fields", {}) or {}
            history_text = str(payload.get("history") or "").strip()
            history_summary = ""
            if history_text:
                first_line = next((line.strip() for line in history_text.splitlines() if line.strip()), "")
                history_summary = first_line[:157].rstrip() + "..." if len(first_line) > 160 else first_line
            summaries.append({
                "id": event.id,
                "title": fields.get("title") or fields.get("correlation_search") or event.source,
                "correlation_search": fields.get("correlation_search"),
                "host": fields.get("host") or event.host,
                "user": fields.get("user") or fields.get("username"),
                "urgency": fields.get("urgency"),
                "disposition": fields.get("disposition"),
                "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
                "history_summary": history_summary,
            })

        return summaries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.post("/api/db/notables/backfill-closure-notes", tags=["Database"])
def backfill_historical_closure_notes():
    """Create concise closure summaries for historical notables missing one.

    Historical pasted notables predate the structured closure-note workflow, so
    this backfill stores the summary in the notable payload and also records a
    ClosureNote row using a stable synthetic historical case id.
    """
    db = None
    try:
        from db.models import ClosureNote, SessionLocal, SplunkEvent

        db = SessionLocal()
        rows = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.id.asc()).all()

        generated = []
        skipped = []
        now = datetime.datetime.utcnow()
        for event in rows:
            try:
                payload = json.loads(event.raw or "{}")
            except Exception:
                continue
            if not payload.get("historical"):
                continue

            existing_history = str(payload.get("history") or "").strip()
            synthetic_case_id = f"HISTORICAL-NOTABLE-{event.id}"
            if existing_history:
                skipped.append(event.id)
                continue

            fields = payload.get("fields") or {}
            title = (fields.get("title") or fields.get("correlation_search") or event.source or "Security notable").strip()
            host = (fields.get("host") or fields.get("destination") or event.host or "unknown host").strip()
            user = (fields.get("user") or fields.get("username") or "unknown user").strip()
            process = (fields.get("process") or fields.get("process_name") or fields.get("parent_process") or "the recorded process").strip()
            disposition = (fields.get("disposition") or "historical disposition not specified").strip()
            summary = (
                f"Closed historical notable '{title}' was recorded on {host} for {user}; "
                f"the triggering activity was associated with {process}."
                f" Recorded disposition: {disposition}."
            )

            payload["history"] = summary
            payload["closure_summary"] = summary
            event.raw = json.dumps(payload)

            rule_id = (fields.get("rule_id") or fields.get("correlation_search") or fields.get("rule_name") or "historical_notable").strip()
            note = db.query(ClosureNote).filter(ClosureNote.case_id == synthetic_case_id).first()
            if not note:
                note = ClosureNote(
                    case_id=synthetic_case_id,
                    rule_id=rule_id,
                    analyst_notes="Backfilled from historical notable metadata for display and baseline analysis.",
                    generated_note=summary,
                    status="closed",
                    created_at=now,
                    submitted_at=now,
                )
                db.add(note)
            generated.append(event.id)

        db.commit()
        return {"generated": len(generated), "event_ids": generated, "skipped": len(skipped)}
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        try:
            db.close()
        except Exception:
            pass


@app.get("/api/db/notables/{event_id}", tags=["Database"])
def get_pasted_notable_details(event_id: int):
    """Return full parsed and sanitized details for a closed pasted notable."""
    try:
        from db.models import SessionLocal, SplunkEvent
        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted",
        ).first()
        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")
        payload = json.loads(event.raw or "{}")
        return {
            "id": event.id,
            "historical": bool(payload.get("historical")),
            "fields": payload.get("fields") or {},
            "raw_fields": payload.get("raw_fields") or {},
            "sanitized_text": payload.get("sanitized_text") or "",
            "history": payload.get("history") or "",
            "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
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
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()
        event = db.query(SplunkEvent).filter(
            SplunkEvent.id == event_id,
            SplunkEvent.sourcetype == "splunk:notable:pasted",
        ).first()

        if not event:
            raise HTTPException(status_code=404, detail=f"Pasted notable {event_id} not found")

        # For historical (closed) pasted notables, or events that already
        # back an existing triage case, retain the underlying record in the
        # database and simply hide it from the recent list so stats and
        # triage-source lookups remain valid.
        try:
            payload = json.loads(event.raw) if event.raw else {}
        except Exception:
            payload = {}

        is_historical = bool(payload.get("historical"))

        promoted_case_id = payload.get("promoted_case_id")
        has_triage_case = False
        try:
            if promoted_case_id:
                existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                has_triage_case = existing_case is not None
            else:
                # Fallback to the canonical NOTABLE-<id> pattern used when
                # promoting notables into triage.
                canonical_case_id = f"NOTABLE-{event.id}"
                existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                has_triage_case = existing_case is not None
        except Exception:
            # If the triage lookup fails for any reason, fall back to the
            # historical flag alone to decide whether to delete.
            has_triage_case = False

        if has_triage_case and not is_historical:
            payload["hidden_from_recent"] = True
            event.raw = json.dumps(payload)
        else:
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


@app.post("/api/db/notables/batch-delete", tags=["Database"])
def batch_delete_pasted_notables(payload: Dict[str, Any]):
    """Delete multiple pasted notables in one call.

    Expects JSON payload:
    {"event_ids": [1, 2, 3, ...]}
    """
    try:
        event_ids = payload.get("event_ids") or []
        if not isinstance(event_ids, list) or not event_ids:
            raise HTTPException(status_code=400, detail="event_ids list is required")

        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, SplunkEvent, TriageResult

        db = SessionLocal()
        deleted: List[int] = []
        missing: List[int] = []

        try:
            for raw_id in event_ids:
                try:
                    eid = int(raw_id)
                except Exception:
                    continue
                event = db.query(SplunkEvent).filter(
                    SplunkEvent.id == eid,
                    SplunkEvent.sourcetype == "splunk:notable:pasted",
                ).first()
                if not event:
                    missing.append(eid)
                    continue

                try:
                    payload_raw = json.loads(event.raw) if event.raw else {}
                except Exception:
                    payload_raw = {}

                is_historical = bool(payload_raw.get("historical"))

                promoted_case_id = payload_raw.get("promoted_case_id")
                has_triage_case = False
                try:
                    if promoted_case_id:
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == promoted_case_id).first()
                        has_triage_case = existing_case is not None
                    else:
                        canonical_case_id = f"NOTABLE-{event.id}"
                        existing_case = db.query(TriageResult).filter(TriageResult.case_id == canonical_case_id).first()
                        has_triage_case = existing_case is not None
                except Exception:
                    has_triage_case = False

                if has_triage_case and not is_historical:
                    payload_raw["hidden_from_recent"] = True
                    event.raw = json.dumps(payload_raw)
                else:
                    db.delete(event)

                deleted.append(eid)

            db.commit()
        finally:
            db.close()

        return {"success": True, "deleted": deleted, "missing": missing}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        resolved_rule = _resolve_correlation_rule(db, ESCorrelationRule, correlation_search)
        resolved_rule_id = resolved_rule.rule_id if resolved_rule else None

        analysis_summary = build_triage_analysis_summary(title, fields, notable_time)

        remediation_steps = "Review the sanitized notable evidence, validate disposition, and gather any supporting host/user activity before closure."

        triage_case = TriageResult(
            case_id=case_id,
            rule_name=correlation_search,
            rule_id=resolved_rule_id,
            verdict=verdict,
            confidence_score=confidence,
            analysis_summary=analysis_summary,
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
        from db.models import SessionLocal
        from services.evidence_service import resolve_source_notable_for_case

        db = SessionLocal()
        try:
            return resolve_source_notable_for_case(db, case_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# In api/main.py -> analyze_case()

@app.post("/api/db/analyze", tags=["Database"])
def analyze_case(request: AnalyzeRequest):
    try:
        case_id = request.case_id
        model = request.model
        context = request.context
        requested_analysis_stage = (request.analysis_stage or "initial").strip().lower() or "initial"
        requested_phase_number = max(1, int(request.analysis_phase or 1))

        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQueryResult, ESCorrelationRule, SupportiveQuery, ClosureNote, InvestigationState, AnalysisResult
        from services.ollama_service import get_ollama_client

        db = SessionLocal()

        # 1. Anchor on the triage case
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            db.close()
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        previous_state_record = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        previous_state_payload = _serialize_investigation_state_record(previous_state_record) if previous_state_record else {}

        # 2. Fetch Detection Science from ESCorrelationRule
        detection_rule = None
        if case.rule_id:
            detection_rule = db.query(ESCorrelationRule).filter(ESCorrelationRule.rule_id == case.rule_id).first()

        # If rule_id is missing or could not be resolved, fall back to a
        # relaxed rule_name match similar to the triage promotion logic so
        # Suspicious LotL and related rules still map to their canonical
        # ESCorrelationRule entries even when the case's rule_name includes
        # wrapper text like "Endpoint - <Rule Name> - Rule".
        if not detection_rule and case.rule_name:
            detection_rule = _resolve_correlation_rule(db, ESCorrelationRule, case.rule_name)
            if detection_rule and case.rule_id != detection_rule.rule_id:
                case.rule_id = detection_rule.rule_id
                db.commit()

        # Load pasted-notable events, baselines, and supportive queries
        pasted_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).order_by(SplunkEvent.ingested_at.desc()).all()

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
                    "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
                }
                break

        historical_baselines = []
        correlation_anchor = (case.rule_name or "").strip()
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
                if correlation_anchor in (title, corr):
                    historical_baselines.append({
                        "event_id": event.id,
                        "fields": fields,
                        "history": payload.get("history"),
                        "sanitized_text": payload.get("sanitized_text", ""),
                    })

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
                "query_title": r.query_title,
                "source_system": r.source_system,
                "raw_result": raw,
            })

        # A Stage 3 rerun should test the original case and Phase 1 evidence
        # without making the saved Phase 2 answers part of the input. Keep all
        # rows for rebuilding the durable investigation state below.
        prompt_supportive_results = supportive_results
        if requested_analysis_stage == "initial":
            prompt_supportive_results = [
                item for item in supportive_results
                if (item.get("source_system") or "").strip().lower() != "phase2_manual"
            ]

        # Load prior structured closure notes for this rule to give the model
        # examples of how similar incidents have been closed historically.
        prior_closures = []
        if detection_rule:
            try:
                prior_rows = db.query(ClosureNote).filter(
                    ClosureNote.rule_id == detection_rule.rule_id
                ).order_by(ClosureNote.created_at.desc()).limit(5).all()
            except Exception:
                prior_rows = []

            for note in prior_rows:
                # Map internal status codes back to human-readable dispositions
                status_value = (note.status or "").strip().lower()
                status_to_disposition = {
                    "true_positive": "True Positive - Suspicious Activity",
                    "benign_positive": "Benign Positive - Suspicious But Expected",
                    "false_positive": "False Positive",
                    "other": "Other",
                    "undetermined": "Undetermined",
                }
                disposition_label = status_to_disposition.get(status_value, note.status or "")

                prior_closures.append({
                    "case_id": note.case_id,
                    "status": note.status,
                    "disposition": disposition_label,
                    "analyst_notes": note.analyst_notes or "",
                    "generated_note": note.generated_note or "",
                    "created_at": note.created_at.isoformat() if note.created_at else None,
                })

        # Load supportive SPL definitions tied to the rule so the model
        # can recommend additional queries to validate its hypothesis.
        # Imported Splunk notables may carry a custom UUID instead of the
        # platform rule_id, so resolve the query family from the notable
        # labels/fields before loading the playbook.
        supportive_query_defs = []
        supportive_rule_id = (detection_rule.rule_id if detection_rule else (case.rule_id or "")).strip()
        catalog_supportive_path = os.path.join(get_platform_root(), "supportive_rules.json")
        catalog_rules = []
        if os.path.isfile(catalog_supportive_path):
            try:
                with open(catalog_supportive_path, "r", encoding="utf-8") as rf:
                    catalog_rules = json.load(rf).get("rules") or []
            except Exception as ex:
                print(f"Failed to load supportive rule catalog: {ex}", file=sys.stderr)

        if catalog_rules:
            notable_fields = (source_notable_payload or {}).get("fields") or {}
            anchor_text = " ".join(
                str(value) for value in [
                    case.rule_name,
                    notable_fields.get("correlation_search"),
                    notable_fields.get("title"),
                    notable_fields.get("description"),
                    notable_fields.get("file_path"),
                    notable_fields.get("file_name"),
                    notable_fields.get("process"),
                    notable_fields.get("parent_process"),
                ] if value
            )
            normalized_anchor = _normalize_rule_match_text(anchor_text)
            exact_catalog = next(
                (item for item in catalog_rules
                 if (item.get("rule_id") or "").strip() == supportive_rule_id),
                None,
            )
            if not exact_catalog:
                anchor_tokens = set(normalized_anchor.split())
                best_catalog = None
                best_score = 0
                for item in catalog_rules:
                    candidate_text = " ".join([
                        str(item.get("rule_id") or ""),
                        str(item.get("rule_name") or ""),
                        " ".join(str(q.get("title") or "") for q in (item.get("supportive_queries") or [])),
                    ])
                    candidate_tokens = set(_normalize_rule_match_text(candidate_text).split())
                    score = len(anchor_tokens & candidate_tokens)
                    if score > best_score:
                        best_catalog = item
                        best_score = score
                if best_catalog and best_score >= 2:
                    supportive_rule_id = (best_catalog.get("rule_id") or "").strip()

        if supportive_rule_id and case.rule_id != supportive_rule_id:
            case.rule_id = supportive_rule_id
            # Persist the normalized family ID without expiring the ORM
            # object before the prompt-building code finishes using it.
            db.flush()

        if supportive_rule_id:
            rule_id = supportive_rule_id

            # Base queries explicitly keyed to this rule_id
            supportive_query_defs.extend(
                db.query(SupportiveQuery).filter(SupportiveQuery.rule_id == rule_id).all()
            )

            # LotL family sharing: reuse canonical queries for related "lotl_*" rules
            canonical_lotl_id = "lotl_outbound_connection"
            if rule_id.startswith("lotl_") and rule_id != canonical_lotl_id:
                extras = db.query(SupportiveQuery).filter(
                    SupportiveQuery.rule_id == canonical_lotl_id
                ).all()
                existing_titles = {q.title for q in supportive_query_defs}
                for q in extras:
                    if q.title not in existing_titles:
                        supportive_query_defs.append(q)

            # Merge specialized follow-up queries from the catalog already
            # loaded above. This also works when the DB has no matching
            # SupportiveQuery rows but the checked-in playbook is present.
            try:
                existing_titles = {_normalize_phase2_text(getattr(q, "title", "")) for q in supportive_query_defs}
                for r_entry in catalog_rules:
                    if (r_entry.get("rule_id") or "").strip().lower() == rule_id.lower():
                        for sq in (r_entry.get("supportive_queries") or []):
                            t = sq.get("title") or ""
                            if _normalize_phase2_text(t) not in existing_titles:
                                class VirtualQuery:
                                    def __init__(self, t, d, s, qid=None):
                                        self.id = qid
                                        self.title = t
                                        self.description = d
                                        self.spl_query = s
                                phase_min = int(sq.get("phase_min") or 2)
                                if phase_min <= requested_phase_number:
                                    supportive_query_defs.append(VirtualQuery(t, sq.get("description") or "", sq.get("spl_query") or "", sq.get("id")))
                                    existing_titles.add(_normalize_phase2_text(t))
            except Exception as ex:
                print(f"Failed to merge supportive rule catalog: {ex}", file=sys.stderr)

            if requested_phase_number > 2:
                existing_titles = {
                    _normalize_phase2_text(
                        query_def.get("title") if isinstance(query_def, dict) else getattr(query_def, "title", "")
                    )
                    for query_def in supportive_query_defs
                }
                for query_def in PHASE_SPECIFIC_SUPPORTIVE_QUERIES.get(rule_id, []):
                    title_key = _normalize_phase2_text(query_def.get("title"))
                    if title_key not in existing_titles:
                        supportive_query_defs.append(query_def)
                        existing_titles.add(title_key)

        db.close()

        prior_analysis = (request.prior_analysis or "").strip()
        analysis_stage = requested_analysis_stage
        prior_analysis_marker = "PHASE2_QUERIES_JSON_START"
        prior_analysis_marker_idx = prior_analysis.find(prior_analysis_marker)
        if prior_analysis_marker_idx != -1:
            prior_analysis = prior_analysis[:prior_analysis_marker_idx].strip()
        if len(prior_analysis) > 3000:
            prior_analysis = prior_analysis[:3000].strip()

        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")

        # 3. Ask Ollama only for the reasoning that benefits from an LLM.
        # Verdict/confidence, grounded Phase 2 cards, and closure gating are
        # calculated by deterministic platform logic after this call.
        prompt_intro = build_analysis_prompt_intro(has_prior_analysis=bool(prior_analysis))

        prompt_parts = [
            "You are an expert SOC Analyst triaging a security incident.",
            prompt_intro,
        ]

        if detection_rule:
            prompt_parts.append("\n\n=== DETECTION SCIENCE & CORRELATION LOGIC ===")
            prompt_parts.append(f"Rule ID: {detection_rule.rule_id}")
            prompt_parts.append(f"Rule Name: {detection_rule.rule_name}")
            prompt_parts.append(f"Description / Hypothesis: {detection_rule.description}")
            prompt_parts.append(f"Category / Domain: {detection_rule.category}")
            prompt_parts.append(f"Severity: {detection_rule.severity}")
            if detection_rule.drilldown_fields:
                prompt_parts.append(f"Key Drilldown Fields: {detection_rule.drilldown_fields}")

        prompt_parts.append("\n\n=== CURRENT CASE ===")
        prompt_parts.append(f"Case ID: {case.case_id} | Rule: {case.rule_name} | Initial Verdict: {case.verdict}")
        prompt_parts.append(f"Summary: {case.analysis_summary}")

        if previous_state_payload:
            prompt_parts.append("\n\n=== INVESTIGATION LOOP STATE ===")
            prompt_parts.append(f"Loop Status: {previous_state_payload.get('loop_status')}")
            prompt_parts.append(f"Iteration Count: {previous_state_payload.get('iteration_count')}")
            prompt_parts.append(f"Current Hypothesis: {previous_state_payload.get('current_hypothesis')}")
            prompt_parts.append(f"Provisional Disposition: {previous_state_payload.get('provisional_disposition')}")
            prompt_parts.append(f"Disposition Confidence: {previous_state_payload.get('disposition_confidence')}")
            unresolved = previous_state_payload.get("unresolved_questions") or []
            if unresolved:
                prompt_parts.append("Open Questions:")
                for item in unresolved[:5]:
                    prompt_parts.append(f"- {item}")
            blockers = previous_state_payload.get("closure_blockers") or []
            if blockers:
                prompt_parts.append("Closure Blockers:")
                for item in blockers[:5]:
                    prompt_parts.append(f"- {item}")
            if unresolved or blockers:
                prompt_parts.append("Prioritize Phase 2 checks that directly resolve these open questions and closure blockers. Every follow-up query should have a clear disposition-changing purpose.")

        if prior_analysis:
            prompt_parts.append("\n\n=== PREVIOUS ANALYSIS HYPOTHESIS ===")
            prompt_parts.append(f"Analysis Stage: {analysis_stage}")
            prompt_parts.append(prior_analysis)

        if source_notable_payload:
            prompt_parts.append("\n\n=== SOURCE NOTABLE EVIDENCE ===")
            if source_notable_payload.get("fields"):
                for k, v in list(source_notable_payload["fields"].items())[:25]:
                    prompt_parts.append(f"- {k}: {v}")
            if source_notable_payload.get("sanitized_text"):
                sanitized_text = str(source_notable_payload["sanitized_text"])[:2500]
                prompt_parts.append(f"\nRaw Sanitized Notable:\n{sanitized_text}")

        if historical_baselines:
            prompt_parts.append("\n\n=== HISTORICAL CLOSURE BASELINES ===")
            prompt_parts.append("Use these prior closed notables only as contextual examples; do not treat them as proof of the current case.")
            for baseline in historical_baselines[:5]:
                fields = baseline.get("fields") or {}
                prompt_parts.append(
                    f"- Event {baseline.get('event_id')}: title={fields.get('title') or fields.get('correlation_search') or 'unknown'}; "
                    f"host={fields.get('host') or fields.get('destination') or 'unknown'}; "
                    f"user={fields.get('user') or fields.get('username') or 'unknown'}; "
                    f"process={fields.get('process') or fields.get('process_name') or 'unknown'}; "
                    f"disposition={fields.get('disposition') or 'unknown'}; "
                    f"closure_summary={str(baseline.get('history') or '')[:500]}"
                )

        if prior_closures:
            prompt_parts.append("\n\n=== PRIOR STRUCTURED CLOSURE NOTES ===")
            prompt_parts.append("Use prior notes as disposition context, not as a substitute for current evidence.")
            for prior in prior_closures[:5]:
                prompt_parts.append(
                    f"- {prior.get('case_id')}: disposition={prior.get('disposition') or prior.get('status')}; "
                    f"note={str(prior.get('generated_note') or '')[:700]}"
                )

        if prompt_supportive_results:
            prompt_parts.append("\n\n=== INVESTIGATION EVIDENCE ===")
            prompt_parts.extend(format_evidence_ledger_entries(prompt_supportive_results))

        if context:
            prompt_parts.append(f"\n\n=== ANALYST CONTEXT ===\n{context[:1000]}")

        composite_prompt = "\n".join(prompt_parts)
        result = client.generate(
            composite_prompt,
            model=model,
            temperature=0.1,
            # Section 4 (Per-Evidence Assessment) adds a per-entry line each;
            # 320 tokens truncated it mid-sentence, losing later verdicts.
            # The service default (OLLAMA_NUM_PREDICT env) is 500, so the
            # per-card flow explicitly needs the larger budget.
            options={"num_predict": int(os.environ.get("OLLAMA_ANALYSIS_NUM_PREDICT", "640"))},
        )
        # Report the tag actually used after auto-resolution so API consumers
        # and the audit trail reflect reality, not the (possibly empty)
        # requested value.
        model = result.get("model") or model

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        response_text = result["response"] or ""
        phase2_queries = _extract_phase2_queries(response_text)


        already_run_titles = _already_run_supportive_titles(prompt_supportive_results)
        phase_query_defs = supportive_query_defs
        if requested_phase_number > 2:
            phase_query_defs = [
                query_def for query_def in supportive_query_defs
                if _normalize_phase2_text(
                    query_def.get("title") if isinstance(query_def, dict) else getattr(query_def, "title", "")
                ) not in already_run_titles
            ]
        blocker_context = " ".join(
            [str(item) for item in (previous_state_payload.get("unresolved_questions") or [])]
            + [str(item) for item in (previous_state_payload.get("closure_blockers") or [])]
        )

        # Later follow-up phases must advance the investigation rather than
        # replaying model-generated titles from an earlier phase. The model
        # still contributes reasoning, but the visible cards come only from
        # unused, grounded playbook definitions for this phase.
        if requested_phase_number > 2:
            # Pass the FULL template pool: when every template has already
            # been run, the phase-filtered list is empty and the variant
            # builder would have nothing to derive question-targeted re-check
            # cards from.
            phase2_queries = _build_question_driven_followup_queries(
                supportive_query_defs,
                previous_state_payload or {},
                prompt_supportive_results,
                requested_phase_number,
            )
            if not phase2_queries:
                phase2_queries = _build_supportive_phase2_fallback(
                    phase_query_defs,
                    f"{prior_analysis} {blocker_context}",
                    response_text,
                    already_run_titles=already_run_titles,
                )
            # NOTE: do NOT run _ground_phase2_queries here. The fallback and
            # question-driven variant builders already emit only playbook-
            # grounded payloads, and grounding would discard the phase-
            # annotated variant titles (they intentionally differ from the
            # template titles), reintroducing the empty Phase 3+ card list.
        elif not phase2_queries:
            phase2_queries = _build_supportive_phase2_fallback(
                phase_query_defs,
                f"{prior_analysis} {blocker_context}",
                response_text,
                already_run_titles=already_run_titles,
            )

        if requested_phase_number <= 2:
            phase2_queries = _ground_phase2_queries(
                phase2_queries,
                phase_query_defs,
                already_run_titles=already_run_titles,
            )
        phase2_queries = _annotate_phase2_targets(phase2_queries, previous_state_payload)
        display_analysis = _sanitize_analysis_text(response_text, phase2_queries)
        investigation_state = _build_investigation_state(
            case,
            display_analysis,
            phase2_queries,
            supportive_results,
            analysis_stage,
            previous_state_payload,
        )
        display_analysis = _apply_investigation_state_to_analysis_text(display_analysis, investigation_state)
        # Persist every completed AI iteration. The browser response is not
        # the system of record: saved prompts/responses let later iterations,
        # reviewers, and closure generation audit exactly what the local model
        # saw and produced.
        db.add(AnalysisResult(
            case_id=case_id,
            model_name=model,
            query=composite_prompt,
            analysis=display_analysis,
            confidence=float(investigation_state.get("disposition_confidence") or 0.0),
        ))
        _upsert_investigation_state(db, InvestigationState, investigation_state)

        # Persist the model's per-card verdicts onto the evidence rows so the
        # ledger itself carries the AI judgment (survives later reloads and is
        # shown in the UI without re-running analysis). NOTE: db was closed()
        # before the model call, so supportive_rows are detached ORM objects —
        # mutating them would be silently lost. Re-query the rows fresh so
        # they belong to the live session.
        timeline_by_id = {
            entry.get("id"): entry
            for entry in (investigation_state.get("evidence_summary") or {}).get("timeline") or []
            if entry.get("id") is not None
        }
        verdict_rows_updated = 0
        if timeline_by_id:
            live_rows = db.query(SupportiveQueryResult).filter(
                SupportiveQueryResult.case_id == case_id
            ).all()
            for row in live_rows:
                entry = timeline_by_id.get(row.id)
                if not entry or entry.get("ai_verdict_source") != "per_card":
                    continue
                try:
                    raw_obj = json.loads(row.raw_result) if row.raw_result else {}
                except Exception:
                    raw_obj = {"result_text": row.raw_result} if row.raw_result else {}
                if not isinstance(raw_obj, dict):
                    continue
                if raw_obj.get("ai_finding_type") == entry.get("finding_type") and raw_obj.get("ai_verdict_rationale") == entry.get("ai_verdict_rationale"):
                    continue
                raw_obj["ai_finding_type"] = entry.get("finding_type")
                raw_obj["ai_verdict_rationale"] = entry.get("ai_verdict_rationale") or ""
                raw_obj["ai_verdict_source"] = "per_card"
                row.raw_result = json.dumps(raw_obj, ensure_ascii=False)
                verdict_rows_updated += 1
        # Commit unconditionally: the AnalysisResult and InvestigationState
        # writes above must persist even when no per-card verdicts were
        # applied (e.g. empty ledger).
        db.commit()

        return {
            "case_id": case_id,
            "model": model,
            "analysis": display_analysis,
            # Keep the summary fields available to the UI and API consumers;
            # the authoritative values are calculated in investigation_state.
            "verdict": investigation_state.get("provisional_disposition") or "undetermined",
            "confidence": float(investigation_state.get("disposition_confidence") or 0.0),
            "analysis_sections": _extract_analysis_sections(response_text),
            "analysis_stage": analysis_stage,
            "used_prior_analysis": bool(prior_analysis),
            "detection_science_applied": bool(detection_rule),
            "baseline_notables_count": len(historical_baselines),
            "supportive_results_count": len(supportive_results),
            "per_card_verdicts_applied": int((investigation_state.get("evidence_summary") or {}).get("per_card_verdicts_applied") or 0),
            "investigation_evidence_count": len(supportive_results),
            "supportive_playbook_available": bool(supportive_query_defs),
            "supportive_rule_id": supportive_rule_id or (case.rule_id or ""),
            "ollama_metrics": {
                "eval_tokens": result.get("tokens", 0),
                "prompt_tokens": result.get("prompt_eval_count", 0),
                "total_duration_seconds": round((result.get("total_duration_ns", 0) or 0) / 1_000_000_000, 1),
                "load_duration_seconds": round((result.get("load_duration_ns", 0) or 0) / 1_000_000_000, 1),
            },
            "supportive_queries": [
                {
                    "id": q.get("id") if isinstance(q, dict) else getattr(q, "id", None),
                    "title": q.get("title") if isinstance(q, dict) else q.title,
                    "description": q.get("description") if isinstance(q, dict) else q.description,
                    "spl_query": q.get("spl_query") if isinstance(q, dict) else q.spl_query,
                }
                for q in supportive_query_defs
            ],
            "prior_closures": prior_closures,
            "phase2_queries": phase2_queries,
            "investigation_state": investigation_state,
        }
    except HTTPException:
        raise
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
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQuery

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

@app.get("/api/db/triage/{case_id}/closure-readiness", tags=["Rules"])
def check_closure_readiness(case_id: str):
    """Evaluate whether a case satisfies all investigation gating rules for closure."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, InvestigationState
        from services.investigation_state import _serialize_investigation_state_record
        from services.closure_service import evaluate_closure_readiness

        db = SessionLocal()
        inv = db.query(InvestigationState).filter(InvestigationState.case_id == case_id).first()
        state_payload = _serialize_investigation_state_record(inv) if inv else {}
        readiness = evaluate_closure_readiness(state_payload)
        db.close()
        return readiness
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/db/closure-note", tags=["Rules"])
def generate_closure_note(request: dict):
    """Generate an operator-ready structured closure note backed by investigation state and evidence."""
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal
        from services.closure_service import generate_structured_closure_note

        rule_id = request.get("rule_id")
        case_id = request.get("case_id")
        field_values = request.get("field_values", {})
        analyst_notes = request.get("analyst_notes", "")
        disposition = request.get("disposition", "Undetermined")
        force_closure = bool(request.get("force_closure", False))

        if not case_id:
            raise HTTPException(status_code=400, detail="case_id is required")

        db = SessionLocal()
        try:
            return generate_structured_closure_note(
                db,
                case_id=case_id,
                rule_id=rule_id,
                field_values=field_values,
                analyst_notes=analyst_notes,
                disposition=disposition,
                force_closure=force_closure,
            )
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            db.close()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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
