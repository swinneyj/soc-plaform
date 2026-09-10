"""Pure investigation-state parsing and evidence scoring helpers."""

import json
import re
from typing import Any, Dict, List, Optional


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
