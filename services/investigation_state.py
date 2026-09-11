"""Pure investigation-state parsing and evidence scoring helpers.

Provides rule-based investigation state tracking, evidence scoring, confidence
guardrails, closure gating, and model output parsing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

NL = chr(10)
NL2 = chr(10) + chr(10)


# Recognized section headings in model analysis outputs, mapping normalized
# aliases to canonical section keys.
CANONICAL_HEADINGS = {
    # 1. Initial Thoughts
    "initial thoughts": "initial thoughts",
    "initial thought": "initial thoughts",
    "initial assessment": "initial thoughts",
    "working hypothesis": "initial thoughts",
    "hypothesis": "initial thoughts",
    "executive summary": "initial thoughts",
    "initial analysis": "initial thoughts",
    "summary": "initial thoughts",
    # 2. Key Questions
    "key questions": "key questions",
    "key question": "key questions",
    "open questions": "key questions",
    "investigative questions": "key questions",
    "unresolved questions": "key questions",
    "questions to answer": "key questions",
    # 3. Investigative Analysis
    "investigative analysis": "investigative analysis",
    "investigation analysis": "investigative analysis",
    "analysis": "investigative analysis",
    "detailed analysis": "investigative analysis",
    "findings": "investigative analysis",
    "evidence evaluation": "investigative analysis",
    "investigation": "investigative analysis",
    # 4. Supportive Query Recommendations (Phase 2 SPL)
    "supportive query recommendations (phase 2 spl)": "supportive query recommendations (phase 2 spl)",
    "supportive query recommendations": "supportive query recommendations (phase 2 spl)",
    "supportive queries recommendations": "supportive query recommendations (phase 2 spl)",
    "phase 2 spl": "supportive query recommendations (phase 2 spl)",
    "phase 2 recommendations": "supportive query recommendations (phase 2 spl)",
    "phase 2 queries": "supportive query recommendations (phase 2 spl)",
    "follow up queries": "supportive query recommendations (phase 2 spl)",
    "follow-up queries": "supportive query recommendations (phase 2 spl)",
    "supportive queries": "supportive query recommendations (phase 2 spl)",
    "recommended queries": "supportive query recommendations (phase 2 spl)",
    "supportive spl": "supportive query recommendations (phase 2 spl)",
    # 5. Triage Verdict
    "triage verdict": "triage verdict",
    "verdict": "triage verdict",
    "disposition": "triage verdict",
    "preliminary verdict": "triage verdict",
    "provisional disposition": "triage verdict",
    "final verdict": "triage verdict",
    "assessment verdict": "triage verdict",
    # 6. Structured Closure Notes
    "structured closure notes": "structured closure notes",
    "structured closure note": "structured closure notes",
    "closure notes": "structured closure notes",
    "closure note": "structured closure notes",
    "closure recommendations": "structured closure notes",
    "closure summary": "structured closure notes",
}

VALID_RESULT_STATUSES = {
    "success",
    "no_results",
    "data_source_unavailable",
    "query_failed",
    "not_run",
    "benign_result",
}

VALID_FINDING_TYPES = {
    "supports",
    "refutes",
    "neutral",
}

VALID_QUESTION_RESOLUTIONS = {
    "not_resolved",
    "partially_resolved",
    "resolved",
}


def _normalize_heading_candidate(line: str) -> str:
    """Normalize a potential section header line."""
    cleaned = (line or "").strip()
    # Remove markdown header prefixes like ### or ##
    cleaned = re.sub(r"^#+\s*", "", cleaned)
    # Remove numbering like 1., 2), [1], etc.
    cleaned = re.sub(r"^(?:\[\d+\]|\d+[\.\)]|\([0-9]+\))\s*", "", cleaned)
    # Remove markdown bold/italic formatting
    cleaned = re.sub(r"^[\*_]+|[\*_]+$", "", cleaned)
    # Strip trailing punctuation, colons, hyphens
    cleaned = cleaned.strip(" 	:.-_")
    return cleaned.lower()


def _extract_analysis_sections(response_text: str) -> Dict[str, str]:
    """Extract standard SOC analysis sections from free-form model text."""
    sections: Dict[str, List[str]] = {}
    current_heading = None
    lines = (response_text or "").splitlines()

    for line in lines:
        normalized = _normalize_heading_candidate(line)
        canonical = CANONICAL_HEADINGS.get(normalized)
        if canonical:
            current_heading = canonical
            sections.setdefault(current_heading, [])
            continue

        # Skip divider lines under headings (e.g. ---, ===)
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
    """Extract individual question lines from the Key Questions section."""
    questions = []
    for raw_line in (section_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Remove bullet markers, numbers, or checklist boxes
        line = re.sub(r"^[-*•]+\s*", "", line)
        line = re.sub(r"^\d+[\.)]\s*", "", line)
        line = re.sub(r"^\[[\sxX]?\]\s*", "", line)
        line = line.strip()
        if line and (line.endswith("?") or len(line) > 10):
            questions.append(line)
    return questions


def _infer_disposition_label(candidate_text: str, fallback: str = "undetermined") -> str:
    """Extract standard disposition label from text."""
    text = (candidate_text or "").strip().lower()
    if "false positive" in text:
        return "false_positive"
    if "benign positive" in text or "benign" in text:
        return "benign"
    if "true positive" in text or "malicious" in text:
        return "malicious"
    if "undetermined" in text or "inconclusive" in text:
        return "undetermined"
    if "suspicious" in text:
        return "suspicious"
    clean_fallback = (fallback or "undetermined").strip().lower()
    return clean_fallback or "undetermined"


def _parse_json_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            return []
    return []


def _parse_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _is_substantive_evidence_value(value: str) -> bool:
    """Return True if string contains real findings beyond placeholders."""
    cleaned = (value or "").strip()
    if not cleaned:
        return False

    normalized = cleaned.lower()
    if normalized in {
        "test", "testing", "todo", "tbd", "n/a", "na", "none",
        "pending", "unknown", "not run", "not_run", "null", "nil"
    }:
        return False

    alnum_count = len(re.sub(r"[^a-z0-9]", "", normalized))
    return alnum_count >= 8


def _summarize_evidence_observation(raw_result: Dict[str, Any]) -> str:
    """Extract a clean short observation summary from raw result payload."""
    analyst_summary = (raw_result.get("analyst_summary") or "").strip()
    result_text = (raw_result.get("result_text") or "").strip()
    result_status = (raw_result.get("result_status") or "").strip().lower()

    if _is_substantive_evidence_value(analyst_summary):
        return analyst_summary[:240]

    if result_status == "no_results":
        return "Query executed successfully: 0 events returned (negative baseline)."
    if result_status == "data_source_unavailable":
        return "Telemetry unavailable: target data source or index is offline/uncollected."
    if result_status == "query_failed":
        return "Query execution failed: check syntax, permissions, or time range."
    if result_status == "benign_result":
        return "Query confirmed expected/authorized benign operational activity."

    if _is_substantive_evidence_value(result_text):
        return result_text[:240]

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


def _evaluate_question_resolution(
    prior_questions: List[str],
    current_analysis_text: str,
    evidence_summaries: List[str],
) -> Tuple[List[str], List[str]]:
    """Determine which prior questions were resolved vs remain open."""
    unresolved: List[str] = []
    resolved: List[str] = []
    lower_analysis = (current_analysis_text or "").lower()
    combined_evidence = " ".join(evidence_summaries).lower()

    for q in prior_questions:
        q_clean = q.strip()
        if not q_clean:
            continue
        q_lower = q_clean.lower()
        keywords = [word for word in re.findall(r"[a-z0-9_]+", q_lower) if len(word) > 3]

        # Check if question appears addressed in analysis or evidence
        evidence_matches = sum(1 for kw in keywords if kw in combined_evidence)
        analysis_matches = sum(1 for kw in keywords if kw in lower_analysis)

        is_addressed = False
        if "resolved" in lower_analysis and any(kw in lower_analysis for kw in keywords):
            is_addressed = True
        elif evidence_matches >= max(2, len(keywords) // 2):
            is_addressed = True

        if is_addressed:
            resolved.append(q_clean)
        else:
            unresolved.append(q_clean)

    return unresolved, resolved


def _build_investigation_state(
    case,
    analysis_text: str,
    phase2_queries: List[Dict[str, Any]],
    supportive_results: List[Dict[str, Any]],
    analysis_stage: str,
    previous_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Calculate the complete investigation loop state with strict guardrails."""
    previous_state = previous_state or {}
    sections = _extract_analysis_sections(analysis_text)
    initial_thoughts = sections.get("initial thoughts", "").strip()
    extracted_questions = _extract_question_items(sections.get("key questions", ""))
    verdict_text = sections.get("triage verdict", "")

    # Carry forward prior unresolved questions
    prior_unresolved = _parse_json_list(previous_state.get("unresolved_questions", []))
    evidence_text_samples = []
    explicitly_resolved_questions = set()

    current_hypothesis = initial_thoughts or (case.analysis_summary or "").strip()
    provisional_disposition = _infer_disposition_label(verdict_text, case.verdict)

    evidence_by_finding = {"supports": 0, "refutes": 0, "neutral": 0}
    evidence_by_status = {
        "success": 0,
        "no_results": 0,
        "data_source_unavailable": 0,
        "query_failed": 0,
        "not_run": 0,
        "benign_result": 0,
    }
    evidence_by_source: Dict[str, int] = {}
    evidence_timeline = []
    titles_with_saved_results = set()

    substantive_evidence_count = 0
    pending_evidence_count = 0
    failed_query_count = 0
    source_unavailable_count = 0
    no_results_count = 0
    benign_result_count = 0
    support_strength = 0
    refute_strength = 0

    closure_blockers: List[str] = []

    for item in supportive_results:
        raw_result = item.get("raw_result") or {}
        if not isinstance(raw_result, dict):
            raw_result = {"result_text": str(raw_result)}

        raw_status = (raw_result.get("result_status") or "success").strip().lower()
        result_status = raw_status if raw_status in VALID_RESULT_STATUSES else "success"
        evidence_by_status[result_status] = evidence_by_status.get(result_status, 0) + 1

        finding_type = (raw_result.get("finding_type") or "neutral").strip().lower()
        if finding_type not in VALID_FINDING_TYPES:
            finding_type = "neutral"

        question_resolution = (raw_result.get("question_resolution") or "not_resolved").strip().lower()
        if question_resolution not in VALID_QUESTION_RESOLUTIONS:
            question_resolution = "not_resolved"
        if question_resolution == "resolved":
            explicitly_resolved_questions.update(
                str(question).strip()
                for question in (raw_result.get("target_questions") or [])
                if str(question).strip()
            )

        source_system = (item.get("source_system") or raw_result.get("source_system") or "splunk").strip() or "splunk"
        title = (item.get("query_title") or raw_result.get("query_title") or "").strip()

        observation_summary = _summarize_evidence_observation(raw_result)
        if observation_summary:
            evidence_text_samples.append(observation_summary)

        has_substantive = False

        if result_status == "query_failed":
            failed_query_count += 1
            closure_blockers.append(f"Query execution failed on '{title}' ({source_system}) - rerun or resolve syntax.")
        elif result_status == "data_source_unavailable":
            source_unavailable_count += 1
            closure_blockers.append(f"Required telemetry unavailable for '{title}' ({source_system}) - data gap exists.")
        elif result_status == "not_run":
            pending_evidence_count += 1
        elif result_status == "no_results":
            no_results_count += 1
            # A query returning no results when searching for lateral movement or malware
            # can be valid negative evidence (refutes hypothesis)
            if finding_type == "refutes":
                has_substantive = True
                substantive_evidence_count += 1
                refute_strength += 1
                evidence_by_finding["refutes"] += 1
            else:
                has_substantive = bool(observation_summary)
                if has_substantive:
                    substantive_evidence_count += 1
                    evidence_by_finding[finding_type] += 1
                else:
                    pending_evidence_count += 1
        elif result_status == "benign_result":
            benign_result_count += 1
            has_substantive = True
            substantive_evidence_count += 1
            refute_strength += 2
            evidence_by_finding["refutes"] += 1
        else:  # success
            if _is_substantive_evidence_value(observation_summary):
                has_substantive = True
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
                "result_status": result_status,
                "finding_type": finding_type,
                "summary": observation_summary or "Pending analyst observation",
                "has_substantive_observation": has_substantive,
                "created_at": item.get("created_at"),
            })
            if has_substantive:
                titles_with_saved_results.add(title.lower())

    # Sort evidence timeline by timestamp / creation
    evidence_timeline.sort(key=lambda x: str(x.get("created_at") or ""), reverse=False)

    # Process questions: preserve active questions from prior state not resolved
    if prior_unresolved:
        active_prior, resolved_prior = _evaluate_question_resolution(
            prior_unresolved,
            analysis_text,
            evidence_text_samples,
        )
        combined_questions = list(dict.fromkeys(extracted_questions + active_prior))
    else:
        combined_questions = extracted_questions

    resolved_normalized = {question.lower() for question in explicitly_resolved_questions}
    unresolved_questions = [
        question for question in combined_questions
        if question.strip().lower() not in resolved_normalized
    ][:6]

    # Strengthen evidence-to-verdict logic
    if support_strength >= 2 and refute_strength == 0:
        provisional_disposition = "malicious"
    elif refute_strength >= 2 and support_strength == 0:
        if case.verdict == "benign" or benign_result_count > 0:
            provisional_disposition = "benign"
        else:
            provisional_disposition = "false_positive"
    elif support_strength > 0 and refute_strength > 0:
        # Conflicting evidence!
        provisional_disposition = "suspicious"
        closure_blockers.append("Conflicting evidence: detection exhibits both supporting and refuting findings.")

    # Hypothesis refinement based on evidence ledger
    if support_strength > refute_strength and support_strength > 0:
        if not current_hypothesis or "weakens" in current_hypothesis:
            current_hypothesis = f"Corroborating evidence ({support_strength} supporting finding{'s' if support_strength != 1 else ''}) reinforces the active threat detection."
    elif refute_strength > support_strength and refute_strength > 0:
        if not current_hypothesis or "reinforces" in current_hypothesis:
            current_hypothesis = f"Collected evidence ({refute_strength} refuting/benign indicator{'s' if refute_strength != 1 else ''}) points to expected administrative or benign activity."

    # Closure blockers check
    if substantive_evidence_count == 0:
        closure_blockers.append("No saved investigative evidence exists yet for this case.")
    if unresolved_questions:
        closure_blockers.append(f"{len(unresolved_questions)} investigative question(s) remain unresolved.")
    if provisional_disposition in {"suspicious", "undetermined"}:
        closure_blockers.append("Disposition is tentative ('suspicious' or 'undetermined') and requires conclusive findings.")
    if evidence_by_finding["supports"] == 0 and evidence_by_finding["refutes"] == 0 and substantive_evidence_count > 0:
        closure_blockers.append("Saved evidence is marked neutral only; no supporting or refuting direction established.")
    if pending_evidence_count > 0:
        closure_blockers.append(f"{pending_evidence_count} saved evidence entry(ies) lack substantive observations.")

    # Recommended next actions
    recommended_next_actions = []
    for query in phase2_queries:
        title = (query.get("title") or "").strip()
        if not title or title.lower() in titles_with_saved_results:
            continue
        recommended_next_actions.append({
            "type": "query",
            "title": title,
            "description": (query.get("description") or "Execute this grounded check and save the finding as evidence.").strip(),
        })

    if pending_evidence_count > 0:
        recommended_next_actions.insert(0, {
            "type": "evidence",
            "title": "Complete pending evidence entries",
            "description": "Record substantive observations and result status on pending queries.",
        })

    if not recommended_next_actions:
        for q in unresolved_questions[:3]:
            recommended_next_actions.append({
                "type": "question",
                "title": "Resolve open inquiry",
                "description": q,
            })

    # Confidence calculation with strict bounds and penalties
    base_confidence = float(case.confidence_score or 0.5)
    confidence = base_confidence

    # Substantive evidence adjustment
    if support_strength >= 2 and refute_strength == 0:
        confidence += min(0.20, support_strength * 0.06)
    elif refute_strength >= 2 and support_strength == 0:
        confidence += min(0.22, refute_strength * 0.07)
    elif substantive_evidence_count > 0:
        confidence += min(0.10, substantive_evidence_count * 0.03)

    # Penalties
    if evidence_by_finding["supports"] > 0 and evidence_by_finding["refutes"] > 0:
        confidence -= 0.15  # Conflicting evidence penalty
    if failed_query_count > 0:
        confidence -= min(0.15, failed_query_count * 0.05)
    if source_unavailable_count > 0:
        confidence -= min(0.15, source_unavailable_count * 0.06)
    if unresolved_questions:
        confidence -= min(0.12, len(unresolved_questions) * 0.02)
        confidence = min(confidence, 0.75)  # Cap if questions unresolved
    if pending_evidence_count > 0:
        confidence -= min(0.08, pending_evidence_count * 0.02)

    # Disposition caps
    if provisional_disposition in {"suspicious", "undetermined"}:
        confidence = min(confidence, 0.72)
    if closure_blockers:
        confidence = min(confidence, 0.76)

    confidence = max(0.10, min(0.95, confidence))

    # Loop status gating
    clean_blockers = list(dict.fromkeys(closure_blockers))

    is_closure_eligible = (
        provisional_disposition in {"benign", "malicious", "false_positive"}
        and confidence >= 0.80
        and substantive_evidence_count >= 2
        and len(clean_blockers) == 0
        and len(unresolved_questions) == 0
        and pending_evidence_count == 0
        and failed_query_count == 0
        and source_unavailable_count == 0
    )

    if is_closure_eligible:
        loop_status = "ready_for_closure"
    elif substantive_evidence_count >= 2 and confidence >= 0.70 and pending_evidence_count == 0 and failed_query_count == 0:
        loop_status = "ready_for_disposition_review"
    elif substantive_evidence_count > 0:
        loop_status = "needs_more_evidence"
    else:
        loop_status = "collecting_evidence"

    evidence_summary = {
        "total_items": len(supportive_results),
        "substantive_items": substantive_evidence_count,
        "pending_items": pending_evidence_count,
        "by_finding": evidence_by_finding,
        "by_status": evidence_by_status,
        "by_source_system": evidence_by_source,
        "recent_titles": evidence_timeline[-5:],
        "timeline": evidence_timeline,
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
        "closure_blockers": clean_blockers,
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
    return NL.join(lines).strip()


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
        return NL.join(lines).strip()

    lines.append(f"Closure-ready disposition: {disposition}.")
    lines.append("Use the saved evidence timeline and required closure fields to generate the final operator note.")
    return NL.join(lines).strip()


def _apply_investigation_state_to_analysis_text(analysis_text: str, investigation_state: Dict[str, Any]) -> str:
    verdict_section = _format_state_verdict_section(investigation_state)
    closure_section = _format_state_closure_section(investigation_state)

    verdict_pattern = re.compile(
        fr"(?:^|{NL})(?:###\s+)?Triage Verdict[\s\S]*?(?={NL}(?:###\s+)?Structured Closure Notes\b|$)",
        flags=re.IGNORECASE,
    )
    closure_pattern = re.compile(
        fr"(?:^|{NL})(?:###\s+)?Structured Closure Notes[\s\S]*$",
        flags=re.IGNORECASE,
    )

    updated = analysis_text or ""
    if verdict_pattern.search(updated):
        updated = verdict_pattern.sub(NL2 + verdict_section + NL2, updated, count=1)
    else:
        updated = f"{updated}{NL2}{verdict_section}".strip()

    if closure_pattern.search(updated):
        updated = closure_pattern.sub(NL2 + closure_section, updated, count=1)
    else:
        updated = f"{updated}{NL2}{closure_section}".strip()

    return updated.strip()
