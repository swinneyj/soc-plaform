"""Shared investigation-flow helpers for the API layer (router split).

Building blocks used by both api/main.py and the focused routers in
api/routes/ (analyze / evidence / promote): phase-2 query planning, evidence
entry validation, triage key-field extraction, the correlation-rule resolver,
and the evidence payload models. Deliberately free of FastAPI app objects so
the routers can import it without circular imports.
"""

import json
import os
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core_lib.utils import get_platform_root
from services.analysis_service import (
    extract_phase2_queries as _extract_phase2_queries_impl,
    ground_phase2_queries as _ground_phase2_queries_impl,
    looks_like_spl_query as _looks_like_spl_query_impl,
    normalize_phase2_text as _normalize_phase2_text,
)

# Same normalization as normalize_phase2_text (lowercase, non-alphanumerics
# -> spaces); the correlation-rule resolver depends on it.
_normalize_rule_match_text = _normalize_phase2_text


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
}# The extract/ground/looks-like-SPL logic lives ONLY in the services layer now
# (services/analysis_service.py) — this module keeps thin aliases for its many
# internal call sites and for historical importers. Do not reintroduce inline
# copies: the earlier twins had drifted (marker-tag-only extraction, different
# default descriptions, different unused-first ordering). Extend the service
# copy instead.


def _extract_phase2_queries(response_text: str) -> List[Dict[str, Any]]:
    return _extract_phase2_queries_impl(response_text)


def _looks_like_spl_query(query_text: str) -> bool:
    return _looks_like_spl_query_impl(query_text)


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

    Thin alias for services.analysis_service.ground_phase2_queries — the
    canonical implementation (dict-or-object template support, containment
    matching, unused-first ordering, ranked fallback) lives there. See the
    note above _extract_phase2_queries.
    """
    return _ground_phase2_queries_impl(
        phase2_queries,
        supportive_query_defs,
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


def _rescope_variant_spl(spl: str, phase_number: int) -> str:
    """Give a re-check variant a genuinely distinct, phase-scoped SPL.

    Re-running an identical query is not a new query: variants re-scope the
    search-time window (``earliest=-<phase>h``) so each iteration can surface
    fresh rows instead of replaying a saved card's SPL under a new title.
    """
    text = (spl or "").strip()
    if not text:
        return text
    window = f"earliest=-{max(1, int(phase_number))}h"
    if re.search(r"\bearliest=\S+", text):
        return re.sub(r"\bearliest=\S+", window, text, count=1)
    pipe_idx = text.find("|")
    if pipe_idx == -1:
        return f"{text} {window}"
    return f"{text[:pipe_idx]}{window} {text[pipe_idx:]}"


def _build_question_driven_followup_queries(
    supportive_query_defs,
    previous_state_payload: Dict[str, Any],
    prompt_supportive_results: List[Dict[str, Any]],
    phase_number: int,
    max_queries: int = 3,
    current_questions: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build follow-up cards for later phases that target the CURRENT open
    questions, even after every playbook template has already been run.

    Strategy, in order:
    1. Unused playbook templates (grounded, never run) — preferred.
    2. Fresh variants of already-run templates: clone the closest-matching
       template for each open question/blocker, annotate it with the phase
       number and the target it addresses, and mark it as a variant so the
       analyst understands it is a re-scoped run (phase-specific time
       window), not a replay.
    3. No explicit targets but the loop has not converged
       (loop_status != ready_for_closure): hypothesis-verification re-checks,
       so a follow-up phase never dead-ends while the case still needs work.

    This guarantees the UI never shows an empty/stale query list — the
    Phase 3+ dead end ("SPL phase degradation", DEVELOPMENT_PLAN §8).
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
    # Questions this analysis just raised are open work NOW — merge them in so
    # the loop reacts immediately instead of one iteration later.
    for question in (current_questions or []):
        text = str(question).strip()
        if text and text not in questions:
            questions.append(text)
    blockers = [
        str(b).strip()
        for b in (previous_state_payload.get("closure_blockers") or [])
        if str(b).strip() and "remain unresolved" not in str(b).lower()
    ]
    targets = questions + blockers

    # 3) No explicit targets but the loop has not converged: keep it alive
    #    with hypothesis-verification re-checks. A follow-up phase must never
    #    dead-end while the case still needs work (the phase-degradation bug).
    if not targets:
        loop_status = (previous_state_payload.get("loop_status") or "").strip().lower()
        if loop_status == "ready_for_closure":
            return []
        hypothesis = (previous_state_payload.get("current_hypothesis") or "").strip()
        targets = [hypothesis or "Verify the disposition-driving evidence before concluding the investigation"]

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
    used_in_batch: set = set()
    for target in targets[:max_queries]:
        target_words = set(re.findall(r"[a-z0-9]+", target.lower()))
        ranked = []
        for candidate in defs:
            candidate_text = " ".join([
                candidate["title"], candidate["description"], candidate["spl"],
            ]).lower()
            overlap = len(target_words & set(re.findall(r"[a-z0-9]+", candidate_text)))
            ranked.append((overlap, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        # Prefer a template this batch has not already re-checked (variety);
        # fall back to the best match so every target still gets a card.
        best = next((c for _o, c in ranked if c["title"] not in used_in_batch), None)
        if best is None and ranked:
            best = ranked[0][1]
        if not best:
            continue
        used_in_batch.add(best["title"])
        short_question = target if len(target) <= 90 else target[:87].rstrip() + "..."
        variants.append({
            "title": f"Phase {phase_number}: {best['title']} (targeted re-check)",
            "spl": _rescope_variant_spl(best["spl"], phase_number),
            "description": (
                f"Re-scoped Phase {phase_number} run of '{best['title']}' targeting: "
                f"\"{short_question}\". Refine the time window or add context before running; the AI will "
                "assess the new result against this target."
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
    finding_type: Optional[str] = Field("neutral", description="Advisory analyst label, stored for the audit trail only — the AI's per-card assessment decides the evidence direction")
    question_resolution: Optional[str] = Field("not_resolved", description="Advisory analyst label, stored for the audit trail only — targeted inquiries resolve when substantive evidence answers them")
    target_questions: List[str] = Field(default_factory=list, description="Open inquiries targeted by this evidence")
    result_status: Optional[str] = Field("success", description="Execution status: success, no_results, data_source_unavailable, query_failed, not_run, benign_result")
    collection_time: Optional[str] = Field(None, description="ISO timestamp when evidence was collected")
    source_system: Optional[str] = Field("splunk", description="Telemetry source system (splunk, mde, defender, edr, firewall, etc.)")


class InvestigationEvidenceBatchPayload(BaseModel):
    entries: List[InvestigationEvidenceEntryPayload] = Field(default_factory=list)
    source_system: str = Field("phase2_manual", description="Source or stage label for this evidence batch")
    replace_existing: bool = Field(True, description="Replace existing evidence for this case and source_system before saving")


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
