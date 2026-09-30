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
                            # Accept the key aliases the api/ layer historically accepted,
                            # so the service copy is the strict superset.
                            title_keys = ("title", "name", "query_name")
                            spl_keys = ("spl", "query", "sql", "code", "kql")
                            desc_keys = ("description", "desc", "notes")
                            title = ""
                            spl = ""
                            desc = ""
                            if isinstance(item, dict):
                                for k in title_keys:
                                    if k in item and (item.get(k) or "").strip():
                                        title = str(item.get(k)).strip()
                                        break
                                for k in spl_keys:
                                    if k in item and (item.get(k) or "").strip():
                                        spl = str(item.get(k)).strip()
                                        break
                                for k in desc_keys:
                                    if k in item and (item.get(k) or "").strip():
                                        desc = str(item.get(k)).strip()
                                        break
                            elif isinstance(item, str):
                                spl = item.strip()
                                title = f"Phase 2 Query {idx}"
                            title = title or f"Phase 2 Query {idx}"
                            if spl:
                                phase2_queries.append({
                                    "title": str(title).strip(),
                                    "spl": str(spl).strip(),
                                    "description": str(desc).strip(),
                                })
        except Exception:
            pass

    return phase2_queries


def looks_like_spl_query(query_text: str) -> bool:
    """Heuristic: does this text look like an SPL query (not SQL, not prose)?"""
    query = (query_text or "").strip().lower()
    if not query:
        return False
    if re.match(r"^select\b", query):
        return False
    spl_markers = ["index=", "|", "sourcetype=", "eventcode=", "tstats", "from datamodel", "search ", "stats ", "table ", "`"]
    return any(marker in query for marker in spl_markers)


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
            # Discard invented SPL; playbook is the only source of truth
            continue

        dedupe_key = normalize_phase2_text(matched["title"])
        if dedupe_key in seen_titles:
            continue

        grounded.append(dict(matched))
        seen_titles.add(dedupe_key)
        if len(grounded) >= max_queries:
            break

    # Re-order: not-yet-run first. When a later follow-up phase is requested,
    # do not keep resurfacing the same saved cards just because the model
    # repeated their titles; prefer an unused playbook query instead.
    grounded.sort(
        key=lambda q: (0 if normalize_phase2_text(q.get("title")) not in already_run else 1)
    )
    unused_grounded = [
        q for q in grounded
        if normalize_phase2_text(q.get("title")) not in already_run
    ]
    if unused_grounded:
        return unused_grounded[:max_queries]

    if grounded and not already_run:
        return grounded[:max_queries]

    # Model suggested nothing usable → ranked fallback from playbook, skip already-run when possible
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
    # Strip the Phase 2 evidence-verdict JSON block (machine-readable only;
    # the human-readable per-card verdicts live in Per-Evidence Assessment).
    cleaned = re.sub(
        r"\*{0,2}PHASE2_EVIDENCE_JSON_START\*{0,2}[\s\S]*?(?:\*{0,2}PHASE2_EVIDENCE_JSON_END\*{0,2}|$)",
        "",
        cleaned,
    )
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




def extract_phase2_evidence_json(response_text: str) -> List[Dict[str, Any]]:
    """Parse the PHASE2_EVIDENCE_JSON block from model output (Phase 2).

    Returns a list of {title, direction, rationale, confidence_delta_hint}
    dicts. Malformed/missing blocks return [] — callers must fall back to the
    existing Per-Evidence Assessment text parser and the global heuristic.
    """
    response_text = response_text or ""
    start_marker = "PHASE2_EVIDENCE_JSON_START"
    end_marker = "PHASE2_EVIDENCE_JSON_END"
    start_idx = response_text.find(start_marker)
    end_idx = response_text.find(end_marker)
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return []

    raw_block = response_text[start_idx + len(start_marker):end_idx]
    arr_start = raw_block.find("[")
    arr_end = raw_block.rfind("]")
    if arr_start == -1 or arr_end == -1 or arr_end <= arr_start:
        return []
    try:
        parsed = json.loads(raw_block[arr_start:arr_end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []

    valid_directions = {"supports", "refutes", "neutral"}
    valid_hints = {"increase", "decrease", "none"}
    verdicts: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        direction = str(item.get("direction") or "").strip().lower()
        if not title or direction not in valid_directions:
            continue
        hint = str(item.get("confidence_delta_hint") or "none").strip().lower()
        verdicts.append({
            "title": title,
            "direction": direction,
            "rationale": str(item.get("rationale") or "").strip(),
            "confidence_delta_hint": hint if hint in valid_hints else "none",
        })
    return verdicts


def build_analysis_prompt_intro(has_prior_analysis: bool) -> str:
    """Single source of truth for the live analysis prompt instructions.

    Used by api/main.py when assembling the /db/analyze prompt. This replaces
    the drifted 6-section copy that used to live here — the live prompt asks
    for exactly three sections and forbids model-generated SPL/JSON, because
    verdicts, grounded Phase 2 cards, and closure gating are computed by
    deterministic platform logic after the model call.
    """
    intro = (
        "Analyze the case using the detection science, raw notable, and saved SPL evidence below.\n\n"
        "Return exactly these four sections:\n"
        "1. Initial Thoughts\n"
        "2. Key Questions\n"
        "3. Investigative Analysis\n"
        "4. Per-Evidence Assessment\n\n"
        "The Per-Evidence Assessment must contain exactly one line per numbered entry "
        "in the INVESTIGATION EVIDENCE section below, quoting that entry's bracketed "
        "index AND title, in the format:\n"
        "[2] Change ticket check — direction=supports — one-sentence rationale grounded in that entry\n"
        "Do not assess notable fields individually and do not renumber the entries. "
        "Judge each entry only on its own observed result.\n\n"
        "Stay under 250 words per section. Use concise evidence-based language. List no more than three key questions. "
        "Do not generate SPL, a verdict score, or closure notes. Distinguish observed facts from inference.\n\n"
        "After the four sections, append a machine-readable verdict block for every numbered entry:\n"
        "PHASE2_EVIDENCE_JSON_START\n"
        "[{\"title\": \"<entry title exactly as numbered above>\", \"direction\": \"supports|refutes|neutral\", "
        "\"rationale\": \"one sentence\", \"confidence_delta_hint\": \"increase|decrease|none\"}]\n"
        "PHASE2_EVIDENCE_JSON_END\n"
        "This JSON block is required and must cover every entry. It is the only JSON you may emit.\n"
    )
    if has_prior_analysis:
        intro += (
            "A PREVIOUS ANALYSIS is included below. Treat it as the current working hypothesis, not as ground truth. "
            "Reassess it against the newest evidence and state what remains unresolved.\n"
        )
    return intro


def format_evidence_ledger_entries(supportive_results: List[Dict[str, Any]]) -> List[str]:
    """Format saved evidence rows for the INVESTIGATION EVIDENCE prompt block.

    Each entry includes its collection status (success / no_results /
    query_failed / data_source_unavailable / not_run) so the model can weigh
    execution facts, not just analyst prose. Evidence direction is
    intentionally NOT included: the platform derives it from the model's own
    analysis text, so feeding it an analyst-entered direction would defeat
    the AI-derived scoring model.
    """
    blocks: List[str] = []
    for idx, res in enumerate(supportive_results, 1):
        raw_result = res.get("raw_result")
        if isinstance(raw_result, dict):
            query_text = (raw_result.get("query_text") or "").strip()
            result_text = (raw_result.get("result_text") or "").strip()
            analyst_summary = (raw_result.get("analyst_summary") or "").strip()
            result_status = (raw_result.get("result_status") or "success").strip()
            block = [f"[{idx}] {res['query_title']} ({res['source_system']})"]
            block.append(f"Collection Status: {result_status}")
            if query_text:
                block.append(f"Query Used:\n{query_text[:1200]}")
            if result_text:
                block.append(f"Observed Result:\n{result_text[:1800]}")
            if analyst_summary:
                block.append(f"Analyst Takeaway:\n{analyst_summary[:800]}")
            blocks.append("\n".join(block))
        else:
            blocks.append(f"[{idx}] {res['query_title']} ({res['source_system']}): {json.dumps(raw_result)}")
    return blocks
