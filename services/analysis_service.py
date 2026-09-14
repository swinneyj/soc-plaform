"""AI Analysis orchestration, prompt composition, grounding, and persistence service.

Orchestrates multi-turn local LLM investigation loops, detection-science prompt
composition, strict Phase 2 query grounding, model output sanitization, and durable
persistence of every analysis iteration.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from services.investigation_state import (
    _apply_investigation_state_to_analysis_text,
    _build_investigation_state,
    _extract_analysis_sections,
    _extract_question_items,
    _infer_disposition_label,
    _serialize_investigation_state_record,
    _upsert_investigation_state,
)

NL = chr(10)
NL2 = chr(10) + chr(10)


def extract_phase2_queries(response_text: str) -> List[Dict[str, Any]]:
    """Extract Phase 2 follow-up query recommendations from model output."""
    response_text = response_text or ""
    phase2_queries: List[Dict[str, Any]] = []

    # 1. Look for explicit marker tags
    start_marker = "PHASE2_QUERIES_JSON_START"
    end_marker = "PHASE2_QUERIES_JSON_END"
    start_idx = response_text.find(start_marker)
    end_idx = response_text.find(end_marker)

    raw_json_str = ""
    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        raw_json_str = response_text[start_idx + len(start_marker):end_idx].strip()
    else:
        # 2. Try extracting from markdown ```json ``` code block
        code_blocks = re.findall(r"```(?:json)?\s*(\[\s*\{[\s\S]*?\}\s*\])\s*```", response_text, re.IGNORECASE)
        if code_blocks:
            raw_json_str = code_blocks[0].strip()
        else:
            # 3. Try finding any JSON array in text
            array_match = re.search(r"(\[\s*\{[\s\S]*?\"(?:title|spl|query)\"[\s\S]*?\}\s*\])", response_text)
            if array_match:
                raw_json_str = array_match.group(1).strip()

    if raw_json_str:
        try:
            arr_start = raw_json_str.find("[")
            arr_end = raw_json_str.rfind("]")
            if arr_start != -1 and arr_end != -1 and arr_end > arr_start:
                candidate = raw_json_str[arr_start:arr_end + 1]
                parsed = json.loads(candidate)
                if isinstance(parsed, list):
                    for idx, item in enumerate(parsed, 1):
                        if isinstance(item, dict):
                            title = item.get("title") or item.get("name") or f"Phase 2 Query {idx}"
                            spl = item.get("spl") or item.get("query") or item.get("kql") or ""
                            desc = item.get("description") or item.get("desc") or ""
                            if spl:
                                phase2_queries.append({
                                    "title": str(title).strip(),
                                    "spl": str(spl).strip(),
                                    "description": str(desc).strip(),
                                })
        except Exception:
            pass

    return phase2_queries


def normalize_phase2_text(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").strip().lower())
    return re.sub(r"\s+", " ", normalized).strip()


def ground_phase2_queries(
    phase2_queries: List[Dict[str, Any]],
    supportive_query_defs: List[Any],
    already_run_titles: Optional[Set[str]] = None,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    """Strictly ground model recommendations onto authorized supportive query templates."""
    already_run = already_run_titles or set()
    title_to_def: Dict[str, Dict[str, Any]] = {}
    spl_to_def: Dict[str, Dict[str, Any]] = {}

    for query_def in supportive_query_defs or []:
        title = (getattr(query_def, "title", "") or "").strip()
        spl_query = (getattr(query_def, "spl_query", "") or "").strip()
        description = (getattr(query_def, "description", "") or "").strip()
        if not title or not spl_query:
            continue
        payload = {
            "title": title,
            "spl": spl_query,
            "description": description or "Execute this grounded check to collect disposition-driving follow-up evidence.",
        }
        title_to_def[normalize_phase2_text(title)] = payload
        spl_to_def[normalize_phase2_text(spl_query)] = payload

    if not title_to_def:
        return []

    grounded: List[Dict[str, Any]] = []
    seen_titles = set()

    for query in phase2_queries or []:
        title = (query.get("title") or "").strip()
        spl_query = (query.get("spl") or "").strip()
        matched = None

        if title:
            matched = title_to_def.get(normalize_phase2_text(title))
        if not matched and spl_query:
            matched = spl_to_def.get(normalize_phase2_text(spl_query))

        # Substring / containment match
        if not matched and title:
            norm = normalize_phase2_text(title)
            for key, payload in title_to_def.items():
                if norm and (norm in key or key in norm):
                    matched = payload
                    break

        if not matched:
            continue

        dedupe_key = normalize_phase2_text(matched["title"])
        if dedupe_key in seen_titles:
            continue

        grounded.append(dict(matched))
        seen_titles.add(dedupe_key)
        if len(grounded) >= max_queries:
            break

    # Prioritize queries that have not been executed yet
    grounded.sort(
        key=lambda q: (0 if normalize_phase2_text(q.get("title")) not in already_run else 1)
    )
    grounded = grounded[:max_queries]

    if grounded:
        return grounded

    # Fallback: rank unused playbook templates
    ranked = []
    for t_norm, payload in title_to_def.items():
        score = 0
        if t_norm not in already_run:
            score += 10
        ranked.append((score, payload))

    ranked.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in ranked[:max_queries]]


def format_grounded_phase2_section(phase2_queries: List[Dict[str, Any]]) -> str:
    lines = ["### Supportive Query Recommendations (Phase 2 SPL)"]
    if not phase2_queries:
        lines.append("Use the grounded Phase 2 cards below to run follow-up checks after reviewing the current hypothesis.")
        return NL2.join(lines)

    lines.append("Use the grounded Phase 2 cards below for follow-up validation. Recommended checks:")
    for idx, q in enumerate(phase2_queries[:3], 1):
        title = (q.get("title") or f"Phase 2 Query {idx}").strip()
        desc = (q.get("description") or "Run this query to collect follow-up evidence.").strip()
        lines.append(f"{idx}. **{title}**: {desc}")

    return NL2.join(lines)


def sanitize_analysis_text(response_text: str, phase2_queries: List[Dict[str, Any]]) -> str:
    """Clean raw model response of JSON markers and inject grounded recommendations."""
    cleaned = response_text or ""
    cleaned = re.sub(
        r"\*{0,2}PHASE2_QUERIES_JSON_START\*{0,2}[\s\S]*?(?:\*{0,2}PHASE2_QUERIES_JSON_END\*{0,2}|$)",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()

    section_text = format_grounded_phase2_section(phase2_queries)
    section_pattern = re.compile(
        fr"(?:^|{NL})(?:###\s+)?Supportive Query Recommendations \(Phase 2 SPL\)[\s\S]*?(?={NL}(?:###\s+)?(?:Triage Verdict|Structured Closure Notes)\b|$)",
        flags=re.IGNORECASE,
    )

    if section_pattern.search(cleaned):
        cleaned = section_pattern.sub(NL2 + section_text + NL2, cleaned, count=1)
    elif phase2_queries:
        triage_pattern = re.compile(fr"{NL}(?:###\s+)?Triage Verdict\b", flags=re.IGNORECASE)
        triage_match = triage_pattern.search(cleaned)
        if triage_match:
            cleaned = cleaned[:triage_match.start()].rstrip() + NL2 + section_text + NL2 + cleaned[triage_match.start():].lstrip()
        else:
            cleaned = f"{cleaned}{NL2}{section_text}".strip()

    return cleaned.strip()


def assemble_analysis_prompt(
    case,
    detection_rule,
    source_notable_payload: Optional[Dict[str, Any]],
    historical_baselines: List[Dict[str, Any]],
    supportive_results: List[Dict[str, Any]],
    supportive_query_defs: List[Any],
    previous_state_payload: Dict[str, Any],
    prior_analysis: str,
    analysis_stage: str,
    catalog_text: str,
    prior_closures: List[Dict[str, Any]],
    context: str,
) -> str:
    """Assemble the complete, detection-science grounded analysis prompt."""
    prompt_intro = (
        f"Analyze the security incident using the provided Detection Science, raw notable data, supportive query findings, and supportive SPL/KQL templates.{NL2}"
        f"Your response MUST be strictly structured into the following 6 sections in order:{NL}"
        f"1. Initial Thoughts{NL}"
        f"2. Key Questions{NL}"
        f"3. Investigative Analysis{NL}"
        f"4. Supportive Query Recommendations (Phase 2 SPL){NL}"
        f"5. Triage Verdict{NL}"
        f"6. Structured Closure Notes{NL2}"
        f"EVIDENCE EVALUATION RULES:{NL}"
        f"- Explicitly distinguish the status of every investigative check:{NL}"
        f"  * 'Success': The query returned substantive security logs.{NL}"
        f"  * 'No results': 0 events returned. If looking for lateral movement or persistence, this is valid negative/benign evidence refuting an active breach.{NL}"
        f"  * 'Data source unavailable': The target index or telemetry is missing/uncollected. This is an active investigation blocker; do NOT assume safety.{NL}"
        f"  * 'Query failed': Syntax error or timeout. Treat as an unresolved blocker.{NL}"
        f"  * 'Benign result': Confirmed legitimate administrative activity, strongly supporting a Benign Positive or False Positive disposition.{NL}"
        f"- Do NOT propose invented queries, indexes, or field names outside the provided supportive templates and catalog.{NL}"
        f"- In Section 4, recommend 1 to 3 checks matched by exact title from the supportive queries below.{NL2}"
    )

    if prior_analysis:
        prompt_intro += (
            f"A PREVIOUS ANALYSIS is included below. Treat it as a working hypothesis to test against newest evidence.{NL}"
            f"You MUST explicitly address each previous open question: evaluate whether current evidence resolves it, refutes it, or if it remains unanswered.{NL}"
            f"Tighten the likely disposition and propose only confirmatory or falsifying follow-up queries.{NL2}"
        )

    prompt_intro += (
        f"Additionally, you MUST append a machine-readable JSON block with your recommended queries at the very end:{NL}"
        f"PHASE2_QUERIES_JSON_START{NL}"
        f'[ {{"title": "<exact template title>", "spl": "<query text>", "description": "<rationale>"}} ]{NL}'
        f"PHASE2_QUERIES_JSON_END{NL}"
    )

    prompt_parts = [
        "You are an expert SOC Analyst triaging a security incident with rigorous evidence-based reasoning.",
        prompt_intro,
    ]

    if detection_rule:
        prompt_parts.append(f"{NL2}=== DETECTION SCIENCE & CORRELATION LOGIC ===")
        prompt_parts.append(f"Rule ID: {detection_rule.rule_id}")
        prompt_parts.append(f"Rule Name: {detection_rule.rule_name}")
        prompt_parts.append(f"Description / Threat Hypothesis: {detection_rule.description}")
        prompt_parts.append(f"Category: {detection_rule.category} | Severity: {detection_rule.severity}")
        if detection_rule.drilldown_fields:
            prompt_parts.append(f"Key Drilldown Fields: {detection_rule.drilldown_fields}")
        if detection_rule.required_closure_fields:
            prompt_parts.append(f"Mandatory Closure Fields: {detection_rule.required_closure_fields}")

    prompt_parts.append(f"{NL2}=== CURRENT CASE ===")
    prompt_parts.append(f"Case ID: {case.case_id} | Rule: {case.rule_name} | Initial Verdict: {case.verdict}")
    prompt_parts.append(f"Summary: {case.analysis_summary}")

    if previous_state_payload:
        prompt_parts.append(f"{NL2}=== INVESTIGATION LOOP STATE ===")
        prompt_parts.append(f"Loop Status: {previous_state_payload.get('loop_status')}")
        prompt_parts.append(f"Iteration Count: {previous_state_payload.get('iteration_count')}")
        prompt_parts.append(f"Current Working Hypothesis: {previous_state_payload.get('current_hypothesis')}")
        prompt_parts.append(f"Provisional Disposition: {previous_state_payload.get('provisional_disposition')}")
        prompt_parts.append(f"Confidence: {previous_state_payload.get('disposition_confidence')}")
        unresolved = previous_state_payload.get("unresolved_questions") or []
        if unresolved:
            prompt_parts.append("Prior Unresolved Questions to Address:")
            for item in unresolved[:5]:
                prompt_parts.append(f"- {item}")
        blockers = previous_state_payload.get("closure_blockers") or []
        if blockers:
            prompt_parts.append("Current Closure Blockers:")
            for item in blockers[:5]:
                prompt_parts.append(f"- {item}")

    if prior_analysis:
        prompt_parts.append(f"{NL2}=== PREVIOUS ANALYSIS HYPOTHESIS ===")
        prompt_parts.append(f"Analysis Stage: {analysis_stage}")
        prompt_parts.append(prior_analysis)

    if source_notable_payload:
        prompt_parts.append(f"{NL2}=== SOURCE NOTABLE EVIDENCE ===")
        if source_notable_payload.get("fields"):
            for k, v in list(source_notable_payload["fields"].items())[:25]:
                prompt_parts.append(f"- {k}: {v}")
        if source_notable_payload.get("sanitized_text"):
            prompt_parts.append(f"{NL}Raw Sanitized Notable Logs:{NL}{source_notable_payload['sanitized_text']}")

    if historical_baselines:
        prompt_parts.append(f"{NL2}=== COMPARABLE HISTORICAL CASES ===")
        for idx, b in enumerate(historical_baselines[:3], 1):
            prompt_parts.append(f"[Baseline #{idx}] Fields: {json.dumps(b.get('fields', {}))} | History: {b.get('history')}")

    if supportive_results:
        prompt_parts.append(f"{NL2}=== INVESTIGATION EVIDENCE LEDGER ===")
        for idx, res in enumerate(supportive_results, 1):
            raw = res.get("raw_result") or {}
            if not isinstance(raw, dict):
                raw = {"result_text": str(raw)}
            status = raw.get("result_status") or "success"
            direction = raw.get("finding_type") or "neutral"
            obs = raw.get("analyst_summary") or raw.get("result_text") or "Recorded"
            prompt_parts.append(
                f"[{idx}] {res.get('query_title')} ({res.get('source_system', 'splunk')}){NL}"
                f"    Status: {status.upper()} | Direction: {direction.upper()}{NL}"
                f"    Observation: {obs[:240]}"
            )

    if supportive_query_defs:
        prompt_parts.append(f"{NL2}=== AUTHORIZED SUPPORTIVE QUERY TEMPLATES ===")
        prompt_parts.append("In Section 4, you MUST recommend follow-up checks by title from this list:")
        for idx, q in enumerate(supportive_query_defs, 1):
            desc = getattr(q, "description", "") or ""
            spl = getattr(q, "spl_query", "") or ""
            prompt_parts.append(f"[{idx}] {q.title}: {desc}{NL}Query: {spl}")

    if catalog_text:
        prompt_parts.append(f"{NL2}{catalog_text}")

    if prior_closures:
        prompt_parts.append(f"{NL2}=== PRIOR CLOSURE EXAMPLES ===")
        for idx, note in enumerate(prior_closures[:3], 1):
            prompt_parts.append(
                f"[Closure #{idx}] Disposition: {note.get('disposition')} | Summary: {note.get('generated_note', '')[:200]}"
            )

    if context:
        prompt_parts.append(f"{NL2}=== ANALYST CONTEXT & NOTES ==={NL}{context}")

    return NL.join(prompt_parts)
