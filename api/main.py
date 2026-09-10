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
import time
import re
import io
import zipfile
import ast
from pathlib import Path
from enum import Enum

# Add Tools directory to path
tools_dir = os.path.join(os.path.dirname(__file__), '..', 'Tools')
sys.path.insert(0, tools_dir)

from core_lib.utils import get_platform_root, get_reports_dir, get_archive_dir, get_logs_dir
from services.artifact_guard import validate_artifact_value

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
    model: str = "llama3.1:8b"
    context: str = ""
    prior_analysis: str = ""
    analysis_stage: str = "initial"


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

            # Smaller local models sometimes emit multiple adjacent one-item
            # arrays like `[ {...} ] [ {...} ]`. Recover those arrays one by
            # one so the Phase 2 UI still gets usable queries instead of none.
            for match in re.findall(r"\[[\s\S]*?\]", raw_block):
                try:
                    parsed_match = json.loads(match)
                except Exception:
                    continue

                if not isinstance(parsed_match, list):
                    continue

                for idx, item in enumerate(parsed_match, 1):
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

    return phase2_queries


<<<<<<< HEAD
def _looks_like_spl_query(query_text: str) -> bool:
    query_text = (query_text or "").strip()
    if not query_text:
        return False

    lower_text = query_text.lower()
    rejected_prefixes = (
        "correlation search:",
        "detection fields:",
        "neutral tokens:",
        "rule name:",
        "security domain:",
        "description:",
    )
    if lower_text.startswith(rejected_prefixes):
        return False

    valid_prefixes = (
        "search ",
        "index=",
        "sourcetype=",
        "source=",
        "host=",
        "eventtype=",
        "tag=",
        "| tstats",
        "| from",
        "| datamodel",
    )
    if lower_text.startswith(valid_prefixes):
        return True

    # Permit common Splunk base searches that begin with a macro or parentheses.
    if query_text.startswith("`") or query_text.startswith("("):
        return True

    return False


def _safe_analysis_display(response_text: str) -> str:
    """Hide raw model-written SPL; executable SPL is displayed only in validated cards."""

    response_text = response_text or ""

    section_start = re.search(
        r"(?im)^#{1,6}\s*Supportive Query Recommendations(?:\s*\(Phase 2 SPL\))?\s*$",
        response_text,
    )

    if not section_start:
        return response_text

    section_end = re.search(
        r"(?im)^#{1,6}\s*Triage Verdict\s*$",
        response_text[section_start.end():],
    )

    safe_section = (
        "### Supportive Query Recommendations (Phase 2 SPL)\n\n"
        "Use only the validated Phase 2 query cards below. "
        "Raw model-generated SPL is intentionally not displayed here.\n\n"
    )

    if not section_end:
        return response_text[:section_start.start()] + safe_section

    end_index = section_start.end() + section_end.start()

    return (
        response_text[:section_start.start()]
        + safe_section
        + response_text[end_index:]
    )

def _strip_machine_control_blocks(display_text: str) -> str:
    """Remove incomplete or complete model-only JSON blocks from analyst display."""
    display_text = display_text or ""

    # These blocks are parsed from the original response before display text is built.
    # Small local models may be cut off before emitting an END marker, so remove from
    # the start marker through the end of displayed content.
    for marker in ("PHASE2_QUERIES_JSON_START", "DECISION_GATE_JSON_START"):
        marker_index = display_text.find(marker)
        if marker_index != -1:
            display_text = display_text[:marker_index].rstrip()

    return display_text

def _filter_valid_phase2_queries(queries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid_queries: List[Dict[str, Any]] = []
    seen_signatures = set()
    for item in queries or []:
        spl_value = (item.get("spl") or "").strip()
        if not _looks_like_spl_query(spl_value):
            continue
        if _query_contains_unusable_entity_values(spl_value):
            continue

        normalized_signature = re.sub(r"\s+", " ", spl_value).strip().lower()
        if normalized_signature in seen_signatures:
            continue

        seen_signatures.add(normalized_signature)
        valid_queries.append(item)
    return valid_queries


def _query_contains_unusable_entity_values(query_text: str) -> bool:
    # Reject model templates that look syntactically like SPL but require an
    # analyst to replace invented values before the query can run.
    placeholder_pattern = re.compile(
        r"(?i)(?:"
        r"\byour_[a-z0-9_]+\b|"
        r"<\s*(?:index|sourcetype|host|user|username|process|eventtype)[^>]*>|"
        r"\[\s*(?:index|sourcetype|host|user|username|process|eventtype)[^\]]*\]|"
        r"\b(?:index|sourcetype|source|host|user|username|eventtype)\s*=\s*(?:your_[a-z0-9_]+|<[^>]+>|\[[^\]]+\])"
        r")"
    )
    if placeholder_pattern.search(query_text or ""):
        return True

    entity_field_map = {
        "host": "host",
        "destination": "destination",
        "user": "user",
        "username": "username",
        "Account_Name": "user",
        "src_ip": "source_ip",
        "source_ip": "source_ip",
        "dest_ip": "destination_ip",
        "destination_ip": "destination_ip",
    }
    for field_name, entity_key in entity_field_map.items():
        pattern = re.compile(rf'\b{re.escape(field_name)}\s*=\s*"([^"]+)"', flags=re.IGNORECASE)
        for match in pattern.finditer(query_text or ""):
            candidate = (match.group(1) or "").strip()
            if candidate and not is_usable_primary_entity(entity_key, candidate):
                return True
    return False


def _build_no_results_replacement_queries(
    fields: Dict[str, str],
    rule_name: str = "",
    rule_description: str = "",
) -> List[Dict[str, Any]]:
    """
    Build deterministic replacement pivots after a saved query returns no
    results. These intentionally change investigation scope rather than asking
    the model to restate a failed host, user, or rule-history query.
    """
    fields = fields or {}

    user = (fields.get("user") or fields.get("username") or "").strip()
    detection_text = " ".join([
        rule_name or "",
        rule_description or "",
        fields.get("process") or "",
        fields.get("parent_process") or "",
        fields.get("description") or "",
    ]).lower()

    linux_ssh_key_case = any(
        token in detection_text
        for token in ("linux", "ssh key", "ssh-key", "ssh-keygen", "authorized_keys")
    )

    queries: List[Dict[str, Any]] = []

    if linux_ssh_key_case:
        queries.append({
            "title": "Broad Linux SSH-key activity",
            "spl": (
                'search index=* earliest=-24h latest=now '
                '("ssh-keygen" OR "authorized_keys" OR "ssh-rsa" OR "ssh-ed25519") '
                '| table _time host user sourcetype source process Image ParentImage CommandLine '
                '| sort 0 _time'
            ),
            "description": (
                "The prior pivot returned no results. Broaden to Linux SSH-key "
                "indicators across available telemetry before narrowing again."
            ),
        })

    if user and is_usable_primary_entity("user", user):
        queries.append({
            "title": "Account prevalence across hosts",
            "spl": (
                f'search index=* earliest=-7d latest=now '
                f'(user="{user}" OR username="{user}" OR Account_Name="{user}") '
                '| stats count values(host) as hosts values(sourcetype) as sourcetypes by user '
                '| sort - count'
            ),
            "description": (
                "Compare the account across hosts and data sources. This is a "
                "prevalence pivot, not a repeat of the failed narrow time-window search."
            ),
        })

    queries.append({
        "title": "Recent endpoint activity by security signal",
        "spl": (
            'search index=* earliest=-24h latest=now '
            '("ssh-keygen" OR "authorized_keys" OR "ssh-rsa" OR "ssh-ed25519") '
            '| stats count values(host) as hosts values(user) as users '
            'values(sourcetype) as sourcetypes'
        ),
        "description": (
            "Establish whether the SSH-key indicators appear anywhere in recent "
            "telemetry when the initial rule-history or entity pivot was not productive."
        ),
    })

    seen = set()
    deduplicated: List[Dict[str, Any]] = []
    for query in queries:
        signature = re.sub(r"\s+", " ", (query.get("spl") or "")).strip().lower()
        if not signature or signature in seen:
            continue
        seen.add(signature)
        deduplicated.append(query)

    return deduplicated[:3]

def _build_generic_phase2_queries_from_fields(
    fields: Dict[str, str],
    rule_name: str = "",
    rule_description: str = "",
    parse_assessment: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    generic_queries = list((parse_assessment or {}).get("generic_queries") or [])
    if generic_queries:
        return generic_queries[:3]

    host = (fields.get("host") or fields.get("destination") or "").strip()
    user = (fields.get("user") or fields.get("username") or "").strip()
    process_candidate = (fields.get("process") or "").strip()
    parent_process_candidate = (fields.get("parent_process") or "").strip()

    process_check = validate_artifact_value("process", process_candidate)
    parent_process_check = validate_artifact_value("parent_process", parent_process_candidate)

    process = process_check["value"] if process_check["valid"] else ""
    parent_process = parent_process_check["value"] if parent_process_check["valid"] else ""

    correlation_search = (fields.get("correlation_search") or rule_name or "").strip()

    platform_text = " ".join([
        correlation_search,
        fields.get("title") or "",
        fields.get("description") or "",
        fields.get("security_domain") or "",
        process,
        parent_process,
    ]).lower()

    is_linux_case = any(token in platform_text for token in (
        "linux",
        "bash",
        "ssh-keygen",
        "authorized_keys",
        "/home/",
        "/etc/",
    ))
    correlation_search = (fields.get("correlation_search") or rule_name or "").strip()
    notable_time = (fields.get("time") or "").strip()

    queries: List[Dict[str, str]] = []
    timeline_clauses = []

    if host:
        host_escaped = host.replace('"', '\\"')
        timeline_clauses.append(f'host="{host_escaped}"')
    if user:
        user_escaped = user.replace('"', '\\"')
        timeline_clauses.append(f'(user="{user_escaped}" OR username="{user_escaped}" OR Account_Name="{user_escaped}")')

    if timeline_clauses:
        time_hint = " earliest=-30m latest=+30m" if notable_time else ""
        queries.append({
            "title": "Timeline around the notable",
            "spl": f"search index=* {' OR '.join(timeline_clauses)}{time_hint} | sort 0 _time | table _time host user sourcetype source process parent_process CommandLine",
            "description": "Start broad and establish what else happened around the same host, user, and alert window before narrowing further.",
        })

    if host and user:
        host_escaped = host.replace('"', '\\"')
        user_escaped = user.replace('"', '\\"')
        queries.append({
            "title": "Host and user pivot",
            "spl": f"search index=* host=\"{host_escaped}\" (user=\"{user_escaped}\" OR username=\"{user_escaped}\") earliest=-24h latest=now | stats count values(sourcetype) as sourcetypes values(process) as processes by host user",
            "description": "Narrow on the strongest artifacts together to see whether this pairing is isolated, routine, or part of broader suspicious activity.",
        })
    elif host:
        host_escaped = host.replace('"', '\\"')
        queries.append({
            "title": "Host pivot",
            "spl": f"search index=* host=\"{host_escaped}\" earliest=-24h latest=now | stats count values(user) as users values(process) as processes values(sourcetype) as sourcetypes by host",
            "description": "Narrow on the host to determine whether the activity looks isolated or part of a wider pattern on the endpoint.",
        })
    elif user:
        user_escaped = user.replace('"', '\\"')
        queries.append({
            "title": "User pivot",
            "spl": f"search index=* earliest=-24h latest=now (user=\"{user_escaped}\" OR username=\"{user_escaped}\" OR Account_Name=\"{user_escaped}\") | stats count values(host) as hosts values(process) as processes values(sourcetype) as sourcetypes by user",
            "description": "Narrow on the user to decide whether this behavior is common administration or something anomalous across systems.",
        })

    if (process or parent_process) and not is_linux_case:
        clauses = []
        if parent_process:
            parent_escaped = parent_process.replace('"', '\\"')
            clauses.append(f'ParentImage="*\\\\{parent_escaped}"')
        if process:
            process_escaped = process.replace('"', '\\"')
            clauses.append(f'Image="*\\\\{process_escaped}"')
        queries.append({
            "title": "Process lineage disposition check",
            "spl": "search index=* source=\"XmlWinEventLog:Microsoft-Windows-Sysmon/Operational\" EventCode=1 " + " ".join(clauses) + " | table _time ComputerName User ParentImage Image CommandLine ParentCommandLine | sort - _time",
            "description": "Use process lineage to confirm whether the alert aligns with expected administration or suspicious execution flow.",
        })
    elif correlation_search:
        corr_escaped = correlation_search.replace('"', '\\"')
        queries.append({
            "title": "Recent outcomes for this rule",
            "spl": f'| `incident_review` | search correlation_search="{corr_escaped}" | table _time rule_name urgency status owner disposition host user | sort - _time | head 20',
            "description": "Use similar recent notables to calibrate whether this alert usually closes benignly or needs escalation.",
        })

    if not queries:
        hypothesis = (rule_description or rule_name or "this alert").strip()
        queries.append({
            "title": "Recent related notables",
            "spl": '| `incident_review` | table _time rule_name correlation_search urgency status owner disposition | sort - _time | head 25',
            "description": f"Start broad with recent triage context and compare it to {hypothesis} before choosing a narrower pivot.",
        })

    return queries[:3]
=======
def _normalize_phase2_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


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
        title = (getattr(query_def, "title", "") or "").strip()
        spl_query = (getattr(query_def, "spl_query", "") or "").strip()
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

    # Re-order: not-yet-run first
    grounded.sort(
        key=lambda q: (0 if _normalize_phase2_text(q.get("title")) not in already_run_titles else 1)
    )
    grounded = grounded[:max_queries]

    if grounded:
        return grounded

    # Model suggested nothing usable → ranked fallback from playbook, skip already-run when possible
    return _build_supportive_phase2_fallback(
        supportive_query_defs,
        "",
        "",
        already_run_titles=already_run_titles,
        max_queries=max_queries,
    )


def _format_grounded_phase2_section(phase2_queries: List[Dict[str, Any]]) -> str:
    lines = ["### Supportive Query Recommendations (Phase 2 SPL)"]
    if not phase2_queries:
        lines.append("Use the grounded Phase 2 cards below to run follow-up SPL after reviewing the current hypothesis.")
        return "\n\n".join(lines)

    lines.append("Use the grounded Phase 2 cards below for follow-up SPL. Recommended checks:")
    for idx, query in enumerate(phase2_queries[:3], 1):
        title = (query.get("title") or f"Phase 2 Query {idx}").strip()
        description = (query.get("description") or "Run this query to collect follow-up evidence.").strip()
        lines.append(f"{idx}. {title}: {description}")
    return "\n\n".join(lines)


def _format_state_label(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return "Undetermined"
    if raw == "false_positive":
        return "False Positive"
    return " ".join(part.capitalize() for part in raw.split("_"))


def _format_state_verdict_section(investigation_state: Dict[str, Any]) -> str:
    disposition = _format_state_label(investigation_state.get("provisional_disposition") or "undetermined")
    status = _format_state_label(investigation_state.get("loop_status") or "collecting_evidence")
    confidence = float(investigation_state.get("disposition_confidence") or 0.0)
    blockers = investigation_state.get("closure_blockers") or []

    lines = ["### Triage Verdict", ""]
    lines.append(f"Current evidence-driven disposition: {disposition}.")
    lines.append(f"Investigation loop status: {status} ({confidence * 100:.0f}% confidence).")
    if blockers:
        lines.append("")
        lines.append("Closure remains blocked by:")
        for item in blockers[:4]:
            lines.append(f"- {item}")
    return "\n".join(lines).strip()


def _format_state_closure_section(investigation_state: Dict[str, Any]) -> str:
    disposition = _format_state_label(investigation_state.get("provisional_disposition") or "undetermined")
    status = (investigation_state.get("loop_status") or "collecting_evidence").strip().lower()
    next_actions = investigation_state.get("recommended_next_actions") or []
    blockers = investigation_state.get("closure_blockers") or []

    lines = ["### Structured Closure Notes", ""]
    if status != "ready_for_closure":
        lines.append("Closure note generation is deferred until the investigation state is closure-ready.")
        lines.append(f"Current disposition: {disposition}.")
        if blockers:
            lines.append("")
            lines.append("Outstanding blockers:")
            for item in blockers[:4]:
                lines.append(f"- {item}")
        if next_actions:
            lines.append("")
            lines.append("Next best actions before closure:")
            for item in next_actions[:4]:
                title = (item.get("title") or "Next action").strip()
                description = (item.get("description") or "").strip()
                if description:
                    lines.append(f"- {title}: {description}")
                else:
                    lines.append(f"- {title}")
        return "\n".join(lines).strip()

    lines.append(f"Closure-ready disposition: {disposition}.")
    lines.append("Use the saved evidence timeline and required closure fields to generate the final operator note.")
    return "\n".join(lines).strip()


def _apply_investigation_state_to_analysis_text(analysis_text: str, investigation_state: Dict[str, Any]) -> str:
    verdict_section = _format_state_verdict_section(investigation_state)
    closure_section = _format_state_closure_section(investigation_state)

    verdict_pattern = re.compile(
        r"(?:^|\n)(?:###\s+)?Triage Verdict[\s\S]*?(?=\n(?:###\s+)?Structured Closure Notes\b|$)",
        flags=re.IGNORECASE,
    )
    closure_pattern = re.compile(
        r"(?:^|\n)(?:###\s+)?Structured Closure Notes[\s\S]*$",
        flags=re.IGNORECASE,
    )

    updated = analysis_text or ""
    if verdict_pattern.search(updated):
        updated = verdict_pattern.sub("\n\n" + verdict_section + "\n\n", updated, count=1)
    else:
        updated = f"{updated}\n\n{verdict_section}".strip()

    if closure_pattern.search(updated):
        updated = closure_pattern.sub("\n\n" + closure_section, updated, count=1)
    else:
        updated = f"{updated}\n\n{closure_section}".strip()

    return updated.strip()


def _sanitize_analysis_text(response_text: str, phase2_queries: List[Dict[str, Any]]) -> str:
    cleaned = response_text or ""
    cleaned = re.sub(
        r"\*{0,2}PHASE2_QUERIES_JSON_START\*{0,2}[\s\S]*?(?:\*{0,2}PHASE2_QUERIES_JSON_END\*{0,2}|$)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    section_text = _format_grounded_phase2_section(phase2_queries)
    section_pattern = re.compile(
        r"(?:^|\n)(?:###\s+)?Supportive Query Recommendations \(Phase 2 SPL\)[\s\S]*?(?=\n(?:###\s+)?(?:Triage Verdict|Structured Closure Notes)\b|$)",
        flags=re.IGNORECASE,
    )
    if section_pattern.search(cleaned):
        cleaned = section_pattern.sub("\n\n" + section_text + "\n\n", cleaned, count=1)
    elif phase2_queries:
        triage_pattern = re.compile(r"\n(?:###\s+)?Triage Verdict\b", flags=re.IGNORECASE)
        triage_match = triage_pattern.search(cleaned)
        if triage_match:
            cleaned = cleaned[:triage_match.start()].rstrip() + "\n\n" + section_text + "\n\n" + cleaned[triage_match.start():].lstrip()
        else:
            cleaned = f"{cleaned}\n\n{section_text}".strip()

    return cleaned.strip()


def _extract_analysis_sections(response_text: str) -> Dict[str, str]:
    headings = {
        "initial thoughts",
        "key questions",
        "investigative analysis",
        "supportive query recommendations (phase 2 spl)",
        "triage verdict",
        "structured closure notes",
    }

    sections: Dict[str, List[str]] = {}
    current_heading = None
    lines = (response_text or "").replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        normalized = re.sub(r"^#+\s*", "", line).strip().rstrip(":").lower()
        if normalized in headings:
            current_heading = normalized
            sections.setdefault(current_heading, [])
            continue

        if current_heading and re.match(r"^[-=]{3,}\s*$", line.strip()):
            continue

        if current_heading:
            sections[current_heading].append(line)

    return {
        key: "\n".join(value).strip()
        for key, value in sections.items()
        if "\n".join(value).strip()
    }


def _extract_question_items(section_text: str) -> List[str]:
    questions = []
    for raw_line in (section_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-*•]+\s*", "", line)
        line = re.sub(r"^\d+[\.)]\s*", "", line)
        line = line.strip()
        if line:
            questions.append(line)
    return questions


def _infer_disposition_label(candidate_text: str, fallback: str) -> str:
    text = (candidate_text or "").strip().lower()
    if "false positive" in text:
        return "false_positive"
    if "benign" in text:
        return "benign"
    if "true positive" in text or "malicious" in text:
        return "malicious"
    if "undetermined" in text:
        return "undetermined"
    if "suspicious" in text:
        return "suspicious"
    return (fallback or "undetermined").strip().lower() or "undetermined"


def _parse_json_list(value: str) -> List[Any]:
    try:
        parsed = json.loads(value) if value else []
    except Exception:
        parsed = []
    return parsed if isinstance(parsed, list) else []


def _parse_json_object(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value) if value else {}
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _is_substantive_evidence_value(value: str) -> bool:
    cleaned = (value or "").strip()
    if not cleaned:
        return False

    normalized = cleaned.lower()
    if normalized in {"test", "testing", "todo", "tbd", "n/a", "na", "none", "pending", "unknown"}:
        return False

    alnum_count = len(re.sub(r"[^a-z0-9]", "", normalized))
    return alnum_count >= 8


def _summarize_evidence_observation(raw_result: Dict[str, Any]) -> str:
    analyst_summary = (raw_result.get("analyst_summary") or "").strip()
    result_text = (raw_result.get("result_text") or "").strip()
    if _is_substantive_evidence_value(analyst_summary):
        return analyst_summary[:180]
    if _is_substantive_evidence_value(result_text):
        return result_text[:180]
    return ""


def _serialize_investigation_state_record(record) -> Dict[str, Any]:
    if not record:
        return {}
    return {
        "case_id": record.case_id,
        "rule_id": record.rule_id,
        "current_hypothesis": record.current_hypothesis or "",
        "provisional_disposition": record.provisional_disposition or "undetermined",
        "disposition_confidence": record.disposition_confidence if record.disposition_confidence is not None else 0.0,
        "loop_status": record.loop_status or "collecting_evidence",
        "iteration_count": record.iteration_count or 0,
        "unresolved_questions": _parse_json_list(record.unresolved_questions),
        "closure_blockers": _parse_json_list(record.closure_blockers),
        "recommended_next_actions": _parse_json_list(record.recommended_next_actions),
        "evidence_summary": _parse_json_object(record.evidence_summary),
        "last_analysis_stage": record.last_analysis_stage or "initial",
        "updated_at": record.updated_at.isoformat() if getattr(record, "updated_at", None) else None,
    }


def _build_investigation_state(
    case,
    analysis_text: str,
    phase2_queries: List[Dict[str, Any]],
    supportive_results: List[Dict[str, Any]],
    analysis_stage: str,
    previous_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    previous_state = previous_state or {}
    sections = _extract_analysis_sections(analysis_text)
    initial_thoughts = sections.get("initial thoughts", "").strip()
    key_questions = _extract_question_items(sections.get("key questions", ""))
    verdict_text = sections.get("triage verdict", "")

    current_hypothesis = initial_thoughts or (case.analysis_summary or "").strip()
    provisional_disposition = _infer_disposition_label(verdict_text, case.verdict)

    evidence_by_finding = {"supports": 0, "refutes": 0, "neutral": 0}
    evidence_by_source: Dict[str, int] = {}
    evidence_timeline = []
    titles_with_saved_results = set()
    substantive_evidence_count = 0
    pending_evidence_count = 0
    support_strength = 0
    refute_strength = 0

    for item in supportive_results:
        raw_result = item.get("raw_result") or {}
        finding_type = (raw_result.get("finding_type") or "neutral").strip().lower()
        if finding_type not in evidence_by_finding:
            finding_type = "neutral"
        source_system = (item.get("source_system") or "unknown").strip() or "unknown"
        title = (item.get("query_title") or "").strip()

        observation_summary = _summarize_evidence_observation(raw_result)
        has_substantive_observation = bool(observation_summary)
        if has_substantive_observation:
            substantive_evidence_count += 1
            evidence_by_finding[finding_type] += 1
            evidence_by_source[source_system] = evidence_by_source.get(source_system, 0) + 1
            if finding_type == "supports":
                support_strength += 1
            elif finding_type == "refutes":
                refute_strength += 1
        else:
            pending_evidence_count += 1

        if title:
            evidence_timeline.append({
                "id": item.get("id"),
                "title": title,
                "source_system": source_system,
                "finding_type": finding_type,
                "summary": observation_summary,
                "has_substantive_observation": has_substantive_observation,
                "created_at": item.get("created_at"),
            })

        if has_substantive_observation:
            titles_with_saved_results.add(title.lower())

    evidence_count = substantive_evidence_count
    unresolved_questions = key_questions[:6]

    if support_strength >= 2 and refute_strength == 0:
        provisional_disposition = "malicious" if case.verdict == "malicious" else "suspicious"
    elif refute_strength >= 2 and support_strength == 0:
        if case.verdict == "benign":
            provisional_disposition = "benign"
        else:
            provisional_disposition = "false_positive"

    if support_strength > refute_strength and support_strength > 0 and not current_hypothesis:
        current_hypothesis = "Saved evidence currently supports the active detection hypothesis more than it refutes it."
    elif refute_strength > support_strength and refute_strength > 0 and not current_hypothesis:
        current_hypothesis = "Saved evidence currently weakens the active detection hypothesis and suggests a benign or false-positive path."

    closure_blockers = []
    if evidence_count == 0:
        closure_blockers.append("No saved investigative evidence exists yet for this case.")
    if unresolved_questions:
        closure_blockers.append("Open investigative questions remain unresolved.")
    if provisional_disposition in {"suspicious", "undetermined"}:
        closure_blockers.append("Disposition is still tentative and needs more validating evidence.")
    if evidence_by_finding["supports"] == 0 and evidence_by_finding["refutes"] == 0:
        closure_blockers.append("No evidence has been marked as supporting or refuting the working hypothesis.")
    if pending_evidence_count > 0:
        closure_blockers.append("Some saved query entries do not yet contain substantive analyst observations.")

    recommended_next_actions = []
    for query in phase2_queries:
        title = (query.get("title") or "").strip()
        if not title or title.lower() in titles_with_saved_results:
            continue
        recommended_next_actions.append({
            "type": "query",
            "title": title,
            "description": (query.get("description") or "Run this query and save the result as evidence.").strip(),
        })
    if not recommended_next_actions:
        for question in unresolved_questions[:3]:
            recommended_next_actions.append({
                "type": "question",
                "title": "Resolve open question",
                "description": question,
            })
    if pending_evidence_count > 0:
        recommended_next_actions.insert(0, {
            "type": "evidence",
            "title": "Convert saved query placeholders into real evidence",
            "description": "Replace generic notes like test/TBD with concrete results and mark whether each finding supports or refutes the hypothesis.",
        })

    confidence = float(case.confidence_score or 0.5)
    confidence += min(0.22, substantive_evidence_count * 0.05)
    if support_strength >= 2 and refute_strength == 0:
        confidence += 0.08
    elif refute_strength >= 2 and support_strength == 0:
        confidence += 0.08
    if evidence_by_finding["supports"] and evidence_by_finding["refutes"]:
        confidence -= 0.08
    if pending_evidence_count > 0:
        confidence -= min(0.08, pending_evidence_count * 0.02)
    if provisional_disposition in {"suspicious", "undetermined"}:
        confidence = min(confidence, 0.78)
    confidence = max(0.1, min(0.95, confidence))

    if closure_blockers and evidence_count == 0:
        loop_status = "collecting_evidence"
    elif (
        provisional_disposition in {"benign", "malicious", "false_positive"}
        and confidence >= 0.8
        and evidence_by_finding["supports"] + evidence_by_finding["refutes"] >= 2
        and not unresolved_questions
        and pending_evidence_count == 0
    ):
        loop_status = "ready_for_closure"
    elif (
        evidence_by_finding["supports"] + evidence_by_finding["refutes"] >= 2
        and confidence >= 0.7
        and pending_evidence_count == 0
    ):
        loop_status = "ready_for_disposition_review"
    elif closure_blockers:
        loop_status = "needs_more_evidence"
    else:
        loop_status = "ready_for_disposition_review"

    evidence_summary = {
        "total_items": evidence_count,
        "saved_entries": len(supportive_results),
        "substantive_items": substantive_evidence_count,
        "pending_items": pending_evidence_count,
        "by_finding": evidence_by_finding,
        "by_source_system": evidence_by_source,
        "recent_titles": evidence_timeline[-5:],
        "timeline": evidence_timeline[-8:],
    }

    previous_iterations = int(previous_state.get("iteration_count") or 0)
    return {
        "case_id": case.case_id,
        "rule_id": case.rule_id or "",
        "current_hypothesis": current_hypothesis,
        "provisional_disposition": provisional_disposition,
        "disposition_confidence": round(confidence, 3),
        "loop_status": loop_status,
        "iteration_count": previous_iterations + 1,
        "unresolved_questions": unresolved_questions,
        "closure_blockers": list(dict.fromkeys(closure_blockers)),
        "recommended_next_actions": recommended_next_actions[:5],
        "evidence_summary": evidence_summary,
        "last_analysis_stage": analysis_stage,
    }


def _upsert_investigation_state(db, model_cls, state_payload: Dict[str, Any]):
    record = db.query(model_cls).filter(model_cls.case_id == state_payload["case_id"]).first()
    if record is None:
        record = model_cls(case_id=state_payload["case_id"])
        db.add(record)

    record.rule_id = state_payload.get("rule_id") or ""
    record.current_hypothesis = state_payload.get("current_hypothesis") or ""
    record.provisional_disposition = state_payload.get("provisional_disposition") or "undetermined"
    record.disposition_confidence = state_payload.get("disposition_confidence") or 0.0
    record.loop_status = state_payload.get("loop_status") or "collecting_evidence"
    record.iteration_count = int(state_payload.get("iteration_count") or 0)
    record.unresolved_questions = json.dumps(state_payload.get("unresolved_questions") or [], ensure_ascii=False)
    record.closure_blockers = json.dumps(state_payload.get("closure_blockers") or [], ensure_ascii=False)
    record.recommended_next_actions = json.dumps(state_payload.get("recommended_next_actions") or [], ensure_ascii=False)
    record.evidence_summary = json.dumps(state_payload.get("evidence_summary") or {}, ensure_ascii=False)
    record.last_analysis_stage = state_payload.get("last_analysis_stage") or "initial"
    return record
>>>>>>> 709b54ab936e8211c8884e1b8bdd2af21c0d6e54


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


def _is_small_ollama_model(model_name: str) -> bool:
    normalized = (model_name or "").strip().lower()
    return any(token in normalized for token in ["1b", "1.2b", "1.3b", "mini", "small"])


def _truncate_for_prompt(value: Any, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _round_timing_seconds(value: float) -> float:
    return round(max(value, 0.0), 3)


ALLOWED_EVIDENCE_STATUSES = ("supports", "refutes", "neutral", "no_results", "error")


def _normalize_evidence_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized in ALLOWED_EVIDENCE_STATUSES:
        return normalized
    return "neutral"


def _derive_finding_type_from_status(status: str) -> str:
    normalized = _normalize_evidence_status(status)
    if normalized in {"supports", "refutes", "neutral"}:
        return normalized
    return "neutral"


def _extract_evidence_artifact_fields(supportive_results: List[Dict[str, Any]]) -> Dict[str, str]:
    extracted: Dict[str, str] = {}
    patterns = [
        ("host", r'host\s*=\s*"([^"]+)"'),
        ("host", r'ComputerName\s*=\s*"([^"]+)"'),
        ("destination", r'destination\s*=\s*"([^"]+)"'),
        ("user", r'user\s*=\s*"([^"]+)"'),
        ("username", r'username\s*=\s*"([^"]+)"'),
        ("user", r'Account_Name\s*=\s*"([^"]+)"'),
        ("process", r'Image\s*=\s*"[^"\\]*\\([^"\\]+)"'),
        ("parent_process", r'ParentImage\s*=\s*"[^"\\]*\\([^"\\]+)"'),
    ]

    for item in supportive_results:
        raw = item.get("raw_result") or {}
        if not isinstance(raw, dict):
            continue

        status = _normalize_evidence_status(raw.get("evidence_status") or raw.get("finding_type") or "neutral")
        if status == "error":
            continue

        search_space = "\n".join([
            str(raw.get("query_text") or ""),
            str(raw.get("result_text") or ""),
            str(raw.get("analyst_summary") or ""),
        ])

        for key, pattern in patterns:
            if extracted.get(key):
                continue
            match = re.search(pattern, search_space, flags=re.IGNORECASE)
            if match:
                candidate = match.group(1).strip()
                entity_key = "user" if key == "username" else key
                if not is_usable_primary_entity(entity_key, candidate):
                    continue
                extracted[key] = candidate

    return extracted


def _build_evidence_summary(supportive_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {status: 0 for status in ALLOWED_EVIDENCE_STATUSES}
    highlights: List[str] = []

    for item in supportive_results:
        raw = item.get("raw_result") or {}
        if not isinstance(raw, dict):
            continue

        status = _normalize_evidence_status(raw.get("evidence_status") or raw.get("finding_type") or "neutral")
        counts[status] += 1

        result_text = _truncate_for_prompt(raw.get("result_text") or "", 160)
        analyst_summary = _truncate_for_prompt(raw.get("analyst_summary") or "", 160)
        highlight_body = analyst_summary or result_text
        if highlight_body:
            highlights.append(f"[{status}] {item.get('query_title')}: {highlight_body}")

    return {
        "counts": counts,
        "highlights": highlights[:5],
        "artifacts": _extract_evidence_artifact_fields(supportive_results),
    }


def _extract_decision_gate(response_text: str) -> Optional[Dict[str, Any]]:
    response_text = response_text or ""
    start_marker = "DECISION_GATE_JSON_START"
    end_marker = "DECISION_GATE_JSON_END"
    start_idx = response_text.find(start_marker)
    end_idx = response_text.find(end_marker)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    raw_block = response_text[start_idx + len(start_marker):end_idx].strip()
    try:
        parsed = json.loads(raw_block)
    except Exception:
        return None

    if not isinstance(parsed, dict):
        return None

    still_missing = parsed.get("still_missing") or []
    if not isinstance(still_missing, list):
        still_missing = [str(still_missing)]

    return {
        "enough_to_decide": bool(parsed.get("enough_to_decide")),
        "current_disposition": str(parsed.get("current_disposition") or "Undetermined").strip() or "Undetermined",
        "confidence_summary": str(parsed.get("confidence_summary") or "").strip(),
        "still_missing": [str(item).strip() for item in still_missing if str(item).strip()],
        "next_best_action": str(parsed.get("next_best_action") or "").strip(),
    }


def _build_decision_gate_fallback(
    supportive_results: List[Dict[str, Any]],
    analysis_stage: str,
    response_text: str,
) -> Dict[str, Any]:
    summary = _build_evidence_summary(supportive_results)
    counts = summary["counts"]
    enough_to_decide = False
    disposition = "Undetermined"
    still_missing: List[str] = []
    next_best_action = "Run one more targeted follow-up query and reassess the disposition."

    if counts["supports"] >= 2 and counts["refutes"] == 0:
        enough_to_decide = True
        disposition = "Likely True Positive"
        next_best_action = "Document the supporting evidence and move toward closure unless policy requires one final validation step."
    elif counts["refutes"] >= 2 and counts["supports"] == 0:
        enough_to_decide = True
        disposition = "Likely False Positive or Benign Positive"
        next_best_action = "Document why the alert was expected or unsupported and move toward closure."
    elif counts["supports"] and counts["refutes"]:
        still_missing.append("Resolve the conflict between supporting and refuting evidence before deciding disposition.")

    if counts["error"]:
        still_missing.append("At least one prior SPL attempt failed with an error and needs correction or replacement.")
    if counts["no_results"] and not enough_to_decide:
        still_missing.append("At least one prior query returned no results; widen scope or pivot to a different artifact.")
    if not supportive_results and analysis_stage != "initial":
        still_missing.append("No saved follow-up evidence is available yet for this case.")
    if not still_missing and not enough_to_decide:
        still_missing.append("A disposition-driving fact is still missing from the current evidence set.")

    confidence_bits = []
    if counts["supports"]:
        confidence_bits.append(f"{counts['supports']} supporting evidence item(s)")
    if counts["refutes"]:
        confidence_bits.append(f"{counts['refutes']} refuting evidence item(s)")
    if counts["neutral"]:
        confidence_bits.append(f"{counts['neutral']} neutral evidence item(s)")
    if counts["no_results"]:
        confidence_bits.append(f"{counts['no_results']} no-result check(s)")
    if counts["error"]:
        confidence_bits.append(f"{counts['error']} errored query attempt(s)")
    if not confidence_bits:
        confidence_bits.append("no saved follow-up evidence yet")

    confidence_summary = ", ".join(confidence_bits)
    if response_text:
        confidence_summary = _truncate_for_prompt(confidence_summary, 220)

    return {
        "enough_to_decide": enough_to_decide,
        "current_disposition": disposition,
        "confidence_summary": confidence_summary,
        "still_missing": still_missing,
        "next_best_action": next_best_action,
    }


class InvestigationEvidenceEntryPayload(BaseModel):
    query_title: str = Field(..., description="Short title for the investigative query or evidence item")
    query_text: Optional[str] = Field("", description="SPL or other query text used to gather the evidence")
    result_text: Optional[str] = Field("", description="Key rows, findings, or summary pasted by the analyst")
    analyst_summary: Optional[str] = Field("", description="Analyst takeaway or interpretation of the evidence")
    evidence_status: Optional[str] = Field("neutral", description="Structured evidence status: error, no_results, supports, refutes, or neutral")
    finding_type: Optional[str] = Field("neutral", description="Whether the evidence supports, refutes, or is neutral to the active hypothesis")


class InvestigationEvidenceBatchPayload(BaseModel):
    entries: List[InvestigationEvidenceEntryPayload] = Field(default_factory=list)
    source_system: str = Field("phase2_manual", description="Source or stage label for this evidence batch")
    replace_existing: bool = Field(True, description="Replace existing evidence for this case and source_system before saving")


class InvestigationEvidenceIdsPayload(BaseModel):
    """Payload for deleting one or more evidence rows by id."""
    ids: List[int] = Field(default_factory=list, description="SupportiveQueryResult ids to delete for this case")


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

    # If the parsed host or destination still looks like a whole sentence,
    # recover a tighter host anchor from the description text when possible.
    description = (fields.get("description") or "").strip()
    if description:
        description_host_match = re.search(r"\bon\s+([A-Za-z0-9_.:-]+)", description, flags=re.IGNORECASE)
        description_host = description_host_match.group(1).strip().rstrip(".,:;") if description_host_match else ""
        if description_host and is_usable_primary_entity("host", description_host):
            host_val = (fields.get("host") or "").strip()
            if not is_usable_primary_entity("host", host_val):
                fields["host"] = description_host
            dest_val = (fields.get("destination") or "").strip()
            if not dest_val:
                fields["destination"] = description_host

    return fields


def build_generic_enrichment_queries(fields: Dict[str, str]) -> List[Dict[str, str]]:
    """Build a small, safe set of generic SPL queries with concrete values only."""
    queries: List[Dict[str, str]] = []
    correlation_search = (fields.get("correlation_search") or "").strip()

    host_candidate = (fields.get("host") or "").strip()
    user_candidate = (fields.get("user") or fields.get("username") or "").strip()

    host_check = validate_artifact_value("host", host_candidate)
    user_check = validate_artifact_value("user", user_candidate)

    host = host_check["value"] if host_check["valid"] else ""
    user = user_check["value"] if user_check["valid"] else ""

    process_candidate = (fields.get("process") or "").strip()
    parent_process_candidate = (fields.get("parent_process") or "").strip()

    process_check = validate_artifact_value("process", process_candidate)
    parent_process_check = validate_artifact_value("parent_process", parent_process_candidate)

    process = process_check["value"] if process_check["valid"] else ""
    parent_process = parent_process_check["value"] if parent_process_check["valid"] else ""

    correlation_search = (fields.get("correlation_search") or "").strip()

    platform_text = " ".join([
        correlation_search,
        fields.get("title") or "",
        fields.get("description") or "",
        fields.get("security_domain") or "",
        process,
        parent_process,
    ]).lower()

    is_linux_case = any(token in platform_text for token in (
        "linux",
        "bash",
        "ssh-keygen",
        "authorized_keys",
        "/home/",
        "/etc/",
    ))

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

    if (process or parent_process) and not is_linux_case:
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

    fields = normalize_notable_fields(dict(payload.get("fields", {}) or {}))
    raw_fields = normalize_notable_fields(dict(payload.get("raw_fields") or fields or {}))
    parse_assessment = build_parse_assessment(
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
        return 0.5
    if "false positive" in normalized or "true positive" in normalized or "benign" in normalized:
        return 0.8
    return 0.6


def _normalize_rule_match_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


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
        # Derive verdict breakdown for triage cases
        verdict_rows = db.query(
            TriageResult.verdict,
            func.count(TriageResult.verdict)
        ).group_by(TriageResult.verdict).all()

        # Derive counts for pasted notables, broken down by historical flag
        pasted_events = db.query(SplunkEvent).filter(
            SplunkEvent.sourcetype == "splunk:notable:pasted"
        ).all()

        pasted_notables_total = len(pasted_events)
        pasted_notables_historical = 0
        pasted_notables_open = 0

        for event in pasted_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                payload = {}

            if payload.get("historical"):
                pasted_notables_historical += 1
            else:
                pasted_notables_open += 1

        db.close()
        return {
            "triage_cases": triage_count,
            "splunk_events": splunk_count,
            "analyses": analysis_count,
            "verdict_breakdown": {row[0]: row[1] for row in verdict_rows},
            "pasted_notables_total": pasted_notables_total,
            "pasted_notables_open": pasted_notables_open,
            "pasted_notables_historical": pasted_notables_historical,
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
    by_title: Dict[str, list] = {}
    for r in rows:
        title_key = (r.query_title or "").strip().lower()
        source_key = (r.source_system or "").strip().lower()
        by_key.setdefault((title_key, source_key), []).append(r.id)
        by_title.setdefault(title_key, []).append(r.id)

    for item in timeline:
        if not isinstance(item, dict) or item.get("id"):
            continue
        title_key = (item.get("title") or "").strip().lower()
        source_key = (item.get("source_system") or "").strip().lower()
        ids = by_key.get((title_key, source_key)) or []
        if not ids:
            ids = by_title.get(title_key) or []
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
        if payload.replace_existing:
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
            evidence_status = _normalize_evidence_status(entry.evidence_status or entry.finding_type or "neutral")

            if not title or not (result_text or analyst_summary):
                continue

            if source_system == "phase2_manual" and query_text and not _looks_like_spl_query(query_text):
                continue

            raw_result = json.dumps(
                {
                    "query_text": query_text,
                    "result_text": result_text,
                    "analyst_summary": analyst_summary,
                    "evidence_status": evidence_status,
                    "finding_type": _derive_finding_type_from_status(evidence_status),
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



# Static evidence delete paths MUST be registered before the parameterized
# /evidence/{evidence_id} routes so "batch-delete" / "delete-all" are not
# captured as evidence_id values.
@app.post("/api/db/triage/{case_id}/evidence/batch-delete", tags=["Database"])
def batch_delete_case_evidence(case_id: str, payload: InvestigationEvidenceIdsPayload):
    """Delete one or more saved investigation evidence items and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        ids = [int(i) for i in (payload.ids or []) if i is not None]
        if not ids:
            raise HTTPException(status_code=400, detail="No evidence ids provided")

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        rows = (
            db.query(SupportiveQueryResult)
            .filter(
                SupportiveQueryResult.case_id == case_id,
                SupportiveQueryResult.id.in_(ids),
            )
            .all()
        )
        deleted_ids = [r.id for r in rows]
        for row in rows:
            db.delete(row)
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_ids": deleted_ids,
            "deleted_count": len(deleted_ids),
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
    """Delete all saved investigation evidence for a case and rebuild loop state."""
    db = None
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SupportiveQueryResult

        db = SessionLocal()
        case = db.query(TriageResult).filter(TriageResult.case_id == case_id).first()
        if not case:
            raise HTTPException(status_code=404, detail=f"Case {case_id} not found")

        deleted_count = (
            db.query(SupportiveQueryResult)
            .filter(SupportiveQueryResult.case_id == case_id)
            .delete(synchronize_session=False)
        )
        db.flush()

        investigation_state = _rebuild_investigation_state_from_evidence(
            db, case, analysis_stage="evidence_only"
        )
        db.commit()
        return {
            "success": True,
            "deleted_count": int(deleted_count or 0),
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
<<<<<<< HEAD


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

=======
def build_event_details_context(event_details_text: str) -> Dict[str, Any]:
    """
    Convert a full Incident Review Event Details / detection SPL block into a
    compact, deterministic detection-context summary for local-model prompts.

    The original full Event Details text remains available to the operator;
    this helper extracts only high-value investigation characteristics.
    """
    text = (event_details_text or "").strip()
    if not text:
        return {
            "present": False,
            "indexes": [],
            "sourcetypes": [],
            "direction": "",
            "trigger_processes": [],
            "excluded_processes": [],
            "network_fields": [],
            "sysmon_enrichment": False,
            "allowlist_logic_present": False,
            "decision_question": "",
        }

    def unique(values: List[str], limit: int = 12) -> List[str]:
        seen = set()
        result: List[str] = []
        for value in values:
            cleaned = (value or "").strip().strip('"').strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(cleaned)
            if len(result) >= limit:
                break
        return result

    indexes = unique(re.findall(r'\bindex\s*=\s*"?([A-Za-z0-9_.*-]+)"?', text, re.IGNORECASE))
    sourcetypes = unique(re.findall(r'\bsourcetype\s*=\s*"?([A-Za-z0-9_:.*-]+)"?', text, re.IGNORECASE))

    direction_match = re.search(r'\bDirection\s*=\s*"([^"]+)"', text, re.IGNORECASE)
    direction = direction_match.group(1).strip() if direction_match else ""

    # Keep positive trigger executables separate from NOT ProcessName clauses.
    process_blocks = re.findall(
        r'^\s*(?!NOT\s+)ProcessName\s+IN\s*\((.*?)\)',
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    excluded_blocks = re.findall(
        r'^\s*NOT\s+ProcessName\s+IN\s*\((.*?)\)',
        text,
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )

    def extract_executables(blocks: List[str]) -> List[str]:
        values: List[str] = []
        for block in blocks:
            values.extend(
                re.findall(
                    r"""["']\*?([A-Za-z0-9_.-]+\.exe)\*?["']""",
                    block,
                    re.IGNORECASE,
                )
            )
        return unique(values)

    trigger_processes = extract_executables(process_blocks)
    excluded_processes = extract_executables(excluded_blocks)

    network_fields = []
    for field_name in (
        "RemoteAddress",
        "RemotePort",
        "RemoteHostName",
        "remoteURL",
        "LocalAddress",
        "LocalPort",
    ):
        if re.search(rf'\b{re.escape(field_name)}\b', text, re.IGNORECASE):
            network_fields.append(field_name)

    sysmon_enrichment = bool(
        re.search(r'\bSysmon\b|Microsoft-Windows-Sysmon|sysmon_cmdline|ParentImage', text, re.IGNORECASE)
    )

    allowlist_logic_present = bool(
        re.search(r'\bNOT\s+(?:RemoteAddress|RemotePort|ProcessName)\b|\bcidrmatch\b|\ballowlist\b', text, re.IGNORECASE)
    )

    decision_question = (
        "Determine whether the observed process-to-destination network activity "
        "is authorized, expected, and consistent with normal endpoint behavior."
    )

    return {
        "present": True,
        "indexes": indexes,
        "sourcetypes": sourcetypes,
        "direction": direction,
        "trigger_processes": trigger_processes,
        "excluded_processes": excluded_processes,
        "network_fields": network_fields,
        "sysmon_enrichment": sysmon_enrichment,
        "allowlist_logic_present": allowlist_logic_present,
        "decision_question": decision_question,
    }
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687

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
            summaries.append({
                "id": event.id,
                "title": fields.get("title") or fields.get("correlation_search") or event.source,
                "correlation_search": fields.get("correlation_search"),
                "host": fields.get("host") or event.host,
                "user": fields.get("user") or fields.get("username"),
                "urgency": fields.get("urgency"),
                "disposition": fields.get("disposition"),
                "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
            })

        return summaries
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

        if is_historical or has_triage_case:
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

                if is_historical or has_triage_case:
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
                fields = normalize_notable_fields(dict(payload.get("fields", {}) or {}))
                raw_fields = normalize_notable_fields(dict(payload.get("raw_fields") or fields or {}))
                parse_assessment = build_parse_assessment(
                    raw_fields,
                    payload.get("sanitized_text", ""),
                    payload.get("history") or "",
                )
                return {
                    "event_id": event.id,
                    "historical": payload.get("historical", False),
                    "raw_fields": raw_fields,
                    "fields": fields,
                    "key_fields": extract_triage_key_fields(fields, raw_fields),
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "parse_assessment": parse_assessment,
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

# In api/main.py -> analyze_case()

@app.post("/api/db/analyze", tags=["Database"])
def analyze_case(request: AnalyzeRequest):
    try:
        request_started_at = time.perf_counter()
        case_id = request.case_id
        model = request.model
        context = request.context

        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, TriageResult, SplunkEvent, SupportiveQueryResult, ESCorrelationRule, SupportiveQuery, ClosureNote, InvestigationState
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
        source_notable_fields: Dict[str, str] = {}
        source_notable_parse_assessment: Dict[str, Any] = {}
        for event in pasted_events:
            try:
                payload = json.loads(event.raw) if event.raw else {}
            except Exception:
                continue
            if payload.get("promoted_case_id") == case_id:
                normalized_fields = normalize_notable_fields(dict(payload.get("fields", {}) or {}))
                source_notable_parse_assessment = build_parse_assessment(
                    normalized_fields.copy(),
                    payload.get("sanitized_text", ""),
                    payload.get("history") or "",
                )
                source_notable_payload = {
                    "event_id": event.id,
                    "fields": normalized_fields,
                    "sanitized_text": payload.get("sanitized_text", ""),
                    "history": payload.get("history"),
                    "saved_at": payload.get("saved_at") or (event.ingested_at.isoformat() if event.ingested_at else None),
                }
                source_notable_fields = source_notable_payload.get("fields") or {}
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
                "query_title": r.query_title,
                "source_system": r.source_system,
                "raw_result": raw,
            })

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
        supportive_query_defs = []
        if detection_rule:
            rule_id = (detection_rule.rule_id or "").strip()

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

        db.close()
        context_loaded_at = time.perf_counter()

        prior_analysis = (request.prior_analysis or "").strip()
        analysis_stage = (request.analysis_stage or "initial").strip().lower() or "initial"
        prior_analysis_marker = "PHASE2_QUERIES_JSON_START"
        prior_analysis_marker_idx = prior_analysis.find(prior_analysis_marker)
        if prior_analysis_marker_idx != -1:
            prior_analysis = prior_analysis[:prior_analysis_marker_idx].strip()
        if len(prior_analysis) > 8000:
            prior_analysis = prior_analysis[:8000].strip()

        client = get_ollama_client()
        if not client.available:
            raise HTTPException(status_code=503, detail="Ollama service not available")

        compact_model = _is_small_ollama_model(model)
        has_saved_evidence = bool(supportive_results)
        evidence_summary = _build_evidence_summary(supportive_results)
        evidence_artifact_fields = evidence_summary["artifacts"]

        # 3. Assemble Prompt with Detection Science and explicit response structure
        prompt_intro = (
            "Analyze the case using the provided Detection Science, raw notable data, supportive query results, and supportive SPL templates.\n\n"
            "Your response MUST be structured into the following sections (in order):\n"
            "1. Initial Thoughts\n"
            "2. Key Questions\n"
            "3. Investigative Analysis\n"
            "4. Supportive Query Recommendations (Phase 2 SPL)\n"
            "5. Triage Verdict\n"
<<<<<<< HEAD
            "6. Structured Closure Notes\n\n"
            "In the 'Supportive Query Recommendations (Phase 2 SPL)' section, propose 1-3 follow-up checks an analyst should run AFTER this analysis. "
            "If supportive SPL templates are provided below, you MUST only recommend from that list (match by title). "
            "Do not invent indexes, sourcetypes, field names, SQL, or SPL that is not in the provided templates or data-source catalog. "
            "Prefer templates that are not already covered by saved investigation evidence. "
            "In the narrative Phase 2 section, summarize recommended query titles and purpose only; put full query text only in the machine-readable JSON block.\n\n"
=======
            "6. Decision Gate\n"
            "7. Structured Closure Notes\n\n"
            "In the 'Supportive Query Recommendations (Phase 2 SPL)' section, propose 1-3 specific SPL queries that an analyst can run AFTER this initial analysis to further validate or refute your hypothesis. "
<<<<<<< HEAD
            "For each query, include a short title, the SPL snippet, and one sentence explaining what evidence it is intended to surface. "
            "The SPL must be valid runnable Splunk SPL, not prose, not field labels, and not copied metadata. "
            "Every SPL snippet must start like a real search, for example with 'search', 'index=', 'sourcetype=', 'eventtype=', or a pipe command such as '| tstats'. "
            "Use actual fields from the case evidence and supportive templates when available.\n\n"
=======
            "For each query, include a short title, the SPL snippet (using the rule's detection fields and neutral tokens derived from the provided evidence), and one sentence explaining what evidence it is intended to surface. "
            "If supportive SPL templates are provided, only use those templates and adapt placeholders conservatively; do not invent SQL, table names, or unrelated SPL. "
            "In the narrative Phase 2 section, summarize the recommended query titles and purpose only; the machine-readable JSON block is the only place where full query text should appear.\n\n"
>>>>>>> 709b54ab936e8211c8884e1b8bdd2af21c0d6e54
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687
        )
        if compact_model:
            prompt_intro += (
                "Keep the narrative concise for a small local model: 1-3 short bullets per section, no long prose, and no more than about 350 words before the JSON block. "
                "Prefer concrete fields and direct analyst actions over explanation.\n\n"
            )
        if prior_analysis:
            prompt_intro += (
                "A PREVIOUS ANALYSIS is included below. Treat it as the current working hypothesis, not as ground truth. "
                "Reassess that hypothesis against the newest evidence, call out what still remains unresolved, and tighten the likely disposition. "
                "Your follow-up queries should progress from broad to narrow: start with one general scoping query that establishes surrounding activity and timeline, then one narrower pivot query on the strongest artifact, then one disposition-driving confirm/refute query if still needed. "
                "Do not jump straight to a conclusion-specific search unless the prior evidence already narrowed the hypothesis enough to justify it.\n\n"
            )
        else:
            prompt_intro += (
                "For a first-pass analysis, start broad and then narrow: begin with one general scoping query around the entity, host, user, process, or time window from the notable, then add narrower follow-up queries only if they meaningfully improve disposition confidence.\n\n"
            )
        prompt_intro += (
            "Additionally, you MUST emit a machine-readable JSON block containing the same phase-2 SPL recommendations so that the UI can surface them as interactive cards. "
            "After your natural-language sections, append a block in the following format exactly (no extra commentary before or after):\n"
            "PHASE2_QUERIES_JSON_START\n"
            "[ {\"title\": \"<short title>\", \"spl\": \"<SPL snippet>\", \"description\": \"<one-line explanation>\"}, ... ]\n"
            "PHASE2_QUERIES_JSON_END\n"
            "DECISION_GATE_JSON_START\n"
            "{\"enough_to_decide\": false, \"current_disposition\": \"Undetermined\", \"confidence_summary\": \"<short rationale>\", \"still_missing\": [\"<missing fact 1>\"], \"next_best_action\": \"<what the analyst should do next>\"}\n"
            "DECISION_GATE_JSON_END\n"
        )
        if has_saved_evidence:
            prompt_intro += (
                "Saved investigation evidence is more trustworthy than the original notable fields. If the original notable conflicts with saved evidence, prefer the saved evidence and explicitly say so. "
                "On a follow-up run, only propose more SPL when a specific missing fact still blocks disposition. If the saved evidence already supports a likely true positive or false positive, you may return an empty JSON array for PHASE2_QUERIES_JSON and explain why no further query is required.\n\n"
            )

        prompt_parts = [
            "You are an expert SOC Analyst triaging a security incident.",
            prompt_intro,
        ]

        if detection_rule:
            prompt_parts.append("\n\n=== DETECTION SCIENCE & CORRELATION LOGIC ===")
            prompt_parts.append(f"Rule ID: {detection_rule.rule_id}")
            prompt_parts.append(f"Rule Name: {detection_rule.rule_name}")
            prompt_parts.append(f"Description / Hypothesis: {_truncate_for_prompt(detection_rule.description, 500 if compact_model else 1500)}")
            prompt_parts.append(f"Category / Domain: {detection_rule.category}")
            prompt_parts.append(f"Severity: {detection_rule.severity}")
            if detection_rule.drilldown_fields:
                prompt_parts.append(f"Key Drilldown Fields: {_truncate_for_prompt(detection_rule.drilldown_fields, 250 if compact_model else 800)}")
            if detection_rule.required_closure_fields:
                prompt_parts.append(f"Mandatory Closure Fields: {_truncate_for_prompt(detection_rule.required_closure_fields, 250 if compact_model else 800)}")
            if detection_rule.closure_template and not compact_model:
                prompt_parts.append(f"Standard Closure Format:\n{detection_rule.closure_template}")

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

        if prior_analysis:
            prompt_parts.append("\n\n=== PREVIOUS ANALYSIS HYPOTHESIS ===")
            prompt_parts.append(f"Analysis Stage: {analysis_stage}")
            prompt_parts.append(prior_analysis)

        if source_notable_payload:
            prompt_parts.append("\n\n=== SOURCE NOTABLE EVIDENCE ===")
            if source_notable_payload.get("fields"):
                field_items = list(source_notable_payload["fields"].items())
                if compact_model:
                    preferred_order = ["title", "correlation_search", "time", "host", "destination", "user", "process", "parent_process", "description", "severity", "urgency", "status"]
                    ranked = []
                    field_map = dict(field_items)
                    for key in preferred_order:
                        if key in field_map:
                            ranked.append((key, field_map[key]))
                    for item in field_items:
                        if item not in ranked:
                            ranked.append(item)
                    field_items = ranked[:12]
                for k, v in field_items:
                    prompt_parts.append(f"- {k}: {_truncate_for_prompt(v, 180 if compact_model else 800)}")
            if source_notable_payload.get("sanitized_text"):
                prompt_parts.append(
                    f"\nRaw Sanitized Notable:\n{_truncate_for_prompt(source_notable_payload['sanitized_text'], 600 if compact_model else 2500)}"
                )

        if has_saved_evidence:
            prompt_parts.append("\n\n=== SAVED EVIDENCE SUMMARY ===")
            counts = evidence_summary["counts"]
            prompt_parts.append(
                "Evidence status counts: "
                f"supports={counts['supports']}, refutes={counts['refutes']}, neutral={counts['neutral']}, "
                f"no_results={counts['no_results']}, error={counts['error']}"
            )
            if evidence_artifact_fields:
                prompt_parts.append("Evidence-derived artifacts:")
                for key, value in evidence_artifact_fields.items():
                    prompt_parts.append(f"- {key}: {_truncate_for_prompt(value, 180)}")
            for line in evidence_summary["highlights"]:
                prompt_parts.append(f"- {line}")

        if historical_baselines and not compact_model:
            prompt_parts.append("\n\n=== HISTORICAL BASELINE EXAMPLES ===")
            for idx, b in enumerate(historical_baselines[:3], 1):
                prompt_parts.append(f"[Baseline {idx}] History/Closure Notes: {_truncate_for_prompt(b.get('history'), 1200)}")

        if supportive_results:
            prompt_parts.append("\n\n=== INVESTIGATION EVIDENCE ===")
            evidence_limit = 2 if compact_model else len(supportive_results)
            for idx, res in enumerate(supportive_results[:evidence_limit], 1):
                raw_result = res.get("raw_result")
                if isinstance(raw_result, dict):
                    query_text = (raw_result.get("query_text") or "").strip()
                    result_text = (raw_result.get("result_text") or "").strip()
                    analyst_summary = (raw_result.get("analyst_summary") or "").strip()
                    finding_type = (raw_result.get("finding_type") or "neutral").strip()
                    block = [f"[{idx}] {res['query_title']} ({res['source_system']})"]
                    evidence_status = _normalize_evidence_status(raw_result.get("evidence_status") or finding_type or "neutral")
                    block.append(f"Evidence Status: {evidence_status}")
                    if query_text:
                        block.append(f"Query Used:\n{_truncate_for_prompt(query_text, 250 if compact_model else 1200)}")
                    if result_text:
                        block.append(f"Observed Result:\n{_truncate_for_prompt(result_text, 250 if compact_model else 1200)}")
                    if analyst_summary:
                        block.append(f"Analyst Takeaway:\n{_truncate_for_prompt(analyst_summary, 180 if compact_model else 800)}")
                    prompt_parts.append("\n".join(block))
                else:
                    prompt_parts.append(f"[{idx}] {res['query_title']} ({res['source_system']}): {_truncate_for_prompt(json.dumps(raw_result), 300 if compact_model else 1200)}")

        if supportive_query_defs:
            prompt_parts.append("\n\n=== RECOMMENDED SUPPORTIVE SPL QUERIES TO VALIDATE HYPOTHESIS ===")
<<<<<<< HEAD
            prompt_parts.append(
                "Phase 2 recommendations MUST use titles from this list only. "
                "The UI will ground suggestions to these exact SPL templates."
            )
            for idx, q in enumerate(supportive_query_defs, 1):
=======
            query_limit = 3 if compact_model else len(supportive_query_defs)
            for idx, q in enumerate(supportive_query_defs[:query_limit], 1):
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687
                desc = q.description or ""
                prompt_parts.append(
                    f"[{idx}] {q.title}: {_truncate_for_prompt(desc, 180 if compact_model else 600)}\nSPL: {_truncate_for_prompt(q.spl_query, 250 if compact_model else 1200)}"
                )

<<<<<<< HEAD
        catalog_payload = _load_data_source_catalog()
        rule_id_for_catalog = ""
        if detection_rule and getattr(detection_rule, "rule_id", None):
            rule_id_for_catalog = detection_rule.rule_id
        catalog_text = _format_catalog_for_prompt(catalog_payload, rule_id_for_catalog)
        if catalog_text:
            prompt_parts.append("\n\n" + catalog_text)

        if prior_closures:
=======
        if prior_closures and not compact_model:
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687
            prompt_parts.append("\n\n=== PRIOR CLOSURE NOTE EXAMPLES FOR THIS RULE ===")
            for idx, note in enumerate(prior_closures, 1):
                header = (
                    f"[Closure {idx}] Case {note['case_id']} | "
                    f"Status: {note['status']} | Disposition: {note['disposition']} | "
                    f"Created: {note['created_at']}"
                )
                prompt_parts.append(header)
                if note["analyst_notes"]:
                    prompt_parts.append(f"Analyst Notes:\n{_truncate_for_prompt(note['analyst_notes'], 800)}")
                if note["generated_note"]:
                    prompt_parts.append(f"Structured Closure Note:\n{_truncate_for_prompt(note['generated_note'], 1200)}")

        if context:
            prompt_parts.append(f"\n\n=== ANALYST CONTEXT ===\n{_truncate_for_prompt(context, 300 if compact_model else 1500)}")

        composite_prompt = "\n".join(prompt_parts)
        prompt_assembled_at = time.perf_counter()
        generation_options = {
            "num_predict": 500 if compact_model else 900,
        }
        primary_inference_started_at = time.perf_counter()
        result = client.generate(composite_prompt, model=model, temperature=0.2, options=generation_options)
        primary_inference_completed_at = time.perf_counter()

        if not result["success"]:
            raise HTTPException(status_code=500, detail=result["error"])

        response_text = result["response"] or ""
        fallback_inference_seconds = 0.0
        postprocess_started_at = time.perf_counter()
        decision_gate = _extract_decision_gate(response_text) or _build_decision_gate_fallback(
            supportive_results,
            analysis_stage,
            response_text,
        )
        needs_more_queries = not bool(decision_gate.get("enough_to_decide"))
        phase2_queries = _extract_phase2_queries(response_text)
        phase2_queries = _filter_valid_phase2_queries(phase2_queries)

        # A saved no-result is a deterministic signal to change query purpose.
        # On follow-up analysis, do not let the model repeat a narrow failed
        # pivot or spend time on a fallback model call.
        no_results_replacements: List[Dict[str, Any]] = []
        if analysis_stage != "initial" and evidence_summary["counts"]["no_results"]:
            replacement_fields = evidence_artifact_fields or source_notable_fields
            no_results_replacements = _build_no_results_replacement_queries(
                replacement_fields,
                rule_name=case.rule_name,
                rule_description=detection_rule.description if detection_rule else "",
            )
            if no_results_replacements:
                phase2_queries = no_results_replacements

        if not phase2_queries and needs_more_queries:
            fallback_prompt_parts = [
                "You are generating follow-up SOC investigation queries from an existing analysis.",
                "Return ONLY a JSON array. Do not include markdown fences, prose, headings, or commentary.",
                "Each JSON item must have keys: title, spl, description.",
                "Generate 1-3 valid runnable Splunk SPL queries.",
                "The first query should usually be broad and scoping, the next should narrow on the strongest artifact, and the last should directly help decide disposition if still needed.",
                "Every spl value must start like real SPL, for example 'search', 'index=', 'sourcetype=', 'eventtype=', or '| tstats'.",
                "Do not output prose fragments such as 'Correlation Search:' or 'Detection Fields:' as the spl value.",
                f"Case ID: {case.case_id}",
                f"Rule: {case.rule_name}",
            ]
            if detection_rule:
                fallback_prompt_parts.append(f"Rule Description: {detection_rule.description}")
                if detection_rule.drilldown_fields:
                    fallback_prompt_parts.append(f"Key Drilldown Fields: {detection_rule.drilldown_fields}")
            if supportive_query_defs:
                fallback_prompt_parts.append("Candidate Supportive Query Templates:")
                for idx, q in enumerate(supportive_query_defs[:5], 1):
                    fallback_prompt_parts.append(f"[{idx}] {q.title}: {q.spl_query}")
            if source_notable_payload and source_notable_payload.get("fields"):
                fallback_prompt_parts.append("Case Fields:")
                for k, v in list(source_notable_payload["fields"].items())[:20]:
                    fallback_prompt_parts.append(f"- {k}: {v}")
            if supportive_results:
                fallback_prompt_parts.append("Saved Investigation Evidence:")
                for idx, res in enumerate(supportive_results[:5], 1):
                    fallback_prompt_parts.append(f"[{idx}] {res['query_title']} ({res['source_system']}): {json.dumps(res['raw_result'])}")
            fallback_prompt_parts.append("Previous/Current Analysis:")
            fallback_prompt_parts.append(prior_analysis or response_text[:6000])
            fallback_prompt_parts.append(
                "Return valid JSON like: [{\"title\":\"...\",\"spl\":\"search ...\",\"description\":\"...\"}]"
            )

            fallback_inference_started_at = time.perf_counter()
            fallback_result = client.generate("\n".join(fallback_prompt_parts), model=model)
            fallback_inference_seconds += time.perf_counter() - fallback_inference_started_at
            if fallback_result.get("success"):
                fallback_response_text = (fallback_result.get("response") or "").strip()
                try:
                    parsed_fallback = json.loads(fallback_response_text)
                    if isinstance(parsed_fallback, list):
                        phase2_queries = _extract_phase2_queries(
                            "PHASE2_QUERIES_JSON_START\n"
                            + fallback_response_text
                            + "\nPHASE2_QUERIES_JSON_END"
                        )
                except Exception:
                    phase2_queries = _extract_phase2_queries(fallback_response_text)
                phase2_queries = _filter_valid_phase2_queries(phase2_queries)

<<<<<<< HEAD
        already_run_titles = _already_run_supportive_titles(supportive_results)

        if not phase2_queries:
=======
        if not phase2_queries and needs_more_queries:
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687
            phase2_queries = _build_supportive_phase2_fallback(
                supportive_query_defs,
                prior_analysis,
                response_text,
                already_run_titles=already_run_titles,
            )

<<<<<<< HEAD
        phase2_queries = _ground_phase2_queries(
            phase2_queries,
            supportive_query_defs,
            already_run_titles=already_run_titles,
        )
=======
<<<<<<< HEAD
        if len(phase2_queries) < 2 and needs_more_queries:
            fallback_fields = evidence_artifact_fields or source_notable_fields
            generic_phase2_queries = _build_generic_phase2_queries_from_fields(
                fallback_fields,
                rule_name=case.rule_name,
                rule_description=detection_rule.description if detection_rule else "",
                parse_assessment=source_notable_parse_assessment,
            )
            existing_signatures = {
                re.sub(r"\s+", " ", (item.get("spl") or "").strip()).lower()
                for item in phase2_queries
                if (item.get("spl") or "").strip()
            }
            for query in generic_phase2_queries:
                signature = re.sub(r"\s+", " ", (query.get("spl") or "").strip()).lower()
                if not signature or signature in existing_signatures:
                    continue
                phase2_queries.append(query)
                existing_signatures.add(signature)
                if len(phase2_queries) >= 3:
                    break

        display_analysis = _safe_analysis_display(response_text)
        display_analysis = _strip_machine_control_blocks(display_analysis)

        response_completed_at = time.perf_counter()
        timing = {
            "total_seconds": _round_timing_seconds(response_completed_at - request_started_at),
            "stage_seconds": {
                "load_case_context_seconds": _round_timing_seconds(context_loaded_at - request_started_at),
                "prompt_assembly_seconds": _round_timing_seconds(prompt_assembled_at - context_loaded_at),
                "primary_inference_seconds": _round_timing_seconds(primary_inference_completed_at - primary_inference_started_at),
                "fallback_inference_seconds": _round_timing_seconds(fallback_inference_seconds),
                "response_postprocess_seconds": _round_timing_seconds(response_completed_at - postprocess_started_at),
            },
            "prompt_chars": len(composite_prompt),
            "response_chars": len(response_text),
            "prompt_eval_count": result.get("prompt_eval_count", 0),
            "eval_count": result.get("tokens", 0),
            "compact_model": compact_model,
        }
=======
        phase2_queries = _ground_phase2_queries(phase2_queries, supportive_query_defs)
>>>>>>> 820a4483140ba8442346fa38cb70783555d52687
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
        _upsert_investigation_state(db, InvestigationState, investigation_state)
        db.commit()
>>>>>>> 709b54ab936e8211c8884e1b8bdd2af21c0d6e54

        return {
            "case_id": case_id,
            "model": model,
            "analysis": display_analysis,
            "analysis_stage": analysis_stage,
            "used_prior_analysis": bool(prior_analysis),
            "detection_science_applied": bool(detection_rule),
            "baseline_notables_count": len(historical_baselines),
            "supportive_results_count": len(supportive_results),
            "investigation_evidence_count": len(supportive_results),
            "supportive_queries": [
                {
                    "id": q.id,
                    "title": q.title,
                    "description": q.description,
                    "spl_query": q.spl_query,
                }
                for q in supportive_query_defs
            ],
            "prior_closures": prior_closures,
            "decision_gate": decision_gate,
            "phase2_queries": phase2_queries,
<<<<<<< HEAD
            "timing": timing,
=======
            "investigation_state": investigation_state,
>>>>>>> 709b54ab936e8211c8884e1b8bdd2af21c0d6e54
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
    # Generate a closure note for a case based on rule template.
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

@app.post("/api/code-review/fix", tags=["AI Analysis"])
def fix_code(payload: dict):
    # Generate fixed/improved version of code based on review.
    try:
        sys.path.insert(0, get_platform_root())
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', 'llama3.1:8b').strip()
        
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
    #     "model": "llama3.1:8b" (optional, defaults to llama3.1:8b),
    #     "instructions": "Optional focus or question for the review"
    # }
    try:
        sys.path.insert(0, get_platform_root())
        from db.models import SessionLocal, CodeReview
        from services.ollama_service import get_ollama_client
        
        code_snippet = payload.get('code_snippet', '').strip()
        language = payload.get('language', 'python').strip()
        model = payload.get('model', 'llama3.1:8b').strip()
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
    model: str = Query("llama3.1:8b"),
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

