"""Phase 4 data migration: normalize legacy judgment values in stored rows.

Before the Phase 4 judgment-flow audit, three surfaces carried analyst /
referring-analyst conclusions as if they were findings:

- ``TriageResult.verdict`` / ``confidence_score`` were written at promote
  time from the pasted ES disposition (the deleted ``derive_triage_verdict``
  / ``derive_triage_confidence`` mappers).
- ``SupportiveQueryResult.raw_result["question_resolution"]`` carried
  analyst resolution labels ("resolved" / "partially_resolved").
- ``investigation_states.evidence_summary["resolved_questions"]`` could
  contain questions resolved *only* by those labels, and the durable
  carry-over kept them resolved forever.

The Phase 4 contract: promoted cases enter as an unverified alert
(``suspicious`` at the 0.80 closure-gate baseline), evidence rows carry the
``not_resolved`` label their save paths write, and question resolution is
derived from substantive evidence executed against targeted inquiries.

This module holds the pure normalization logic (the CLI wrapper lives in
``scripts/normalize_phase4_judgments.py``). Substance detection reuses the
state builder's own helpers so the migration cannot drift from the live
resolution rule. Every changed value is reported so callers can archive
the originals before applying.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Set

from services import investigation_state as isvc

# The only values today's write paths can produce.
PHASE4_INTAKE_VERDICT = "suspicious"
PHASE4_INTAKE_CONFIDENCE = 0.8
PHASE4_RESOLUTION_LABEL = "not_resolved"

# Result statuses the state builder never treats as substantive on their own.
_NON_SUBSTANTIVE_STATUSES = {"query_failed", "data_source_unavailable", "not_run"}


def normalize_question_resolution_label(value: Any) -> str:
    """Map any legacy resolution label onto the Phase 4 contract value."""
    return PHASE4_RESOLUTION_LABEL


def normalize_raw_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a copy of an evidence payload with the resolution label normalized.

    All other fields (including advisory ``finding_type`` and the AI's stored
    ``ai_finding_type``) are preserved untouched.
    """
    updated = dict(raw)
    if (raw.get("question_resolution") or PHASE4_RESOLUTION_LABEL) != PHASE4_RESOLUTION_LABEL:
        updated["question_resolution"] = PHASE4_RESOLUTION_LABEL
    return updated


def row_is_substantive(raw: Dict[str, Any]) -> bool:
    """Mirror the state builder's has_substantive rule for a stored row.

    Consistent with services/investigation_state.py: failed / unavailable /
    not-run rows are never substantive; a no_results row always is (its
    negative-baseline observation — "0 events returned" — is evidence, and
    the builder counts it); success-style rows need a substantive
    observation summary.
    """
    status = (raw.get("result_status") or "success").strip().lower()
    if status in _NON_SUBSTANTIVE_STATUSES:
        return False

    summary = isvc._summarize_evidence_observation(raw)

    if status == "no_results":
        return bool(summary)

    return isvc._is_substantive_evidence_value(summary)


def evidence_backed_questions(raw_results: Iterable[Dict[str, Any]]) -> Set[str]:
    """Questions resolved by substantive evidence executed against them."""
    backed: Set[str] = set()
    for raw in raw_results:
        targets = [
            str(question).strip()
            for question in (raw.get("target_questions") or [])
            if str(question).strip()
        ]
        if not targets:
            continue
        if row_is_substantive(raw):
            backed.update(targets)
    return backed


def parse_raw_result(blob: Any) -> Optional[Dict[str, Any]]:
    """Parse a stored raw_result blob; free-text legacy blobs return None."""
    if isinstance(blob, dict):
        return blob
    if not blob:
        return None
    try:
        parsed = json.loads(blob)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def plan_triage_normalization(case: Any) -> Optional[Dict[str, Any]]:
    """Change plan for one TriageResult row, or None when already conformant."""
    old_verdict = (getattr(case, "verdict", "") or "").strip()
    old_confidence = getattr(case, "confidence_score", None)
    old_confidence = float(old_confidence) if old_confidence is not None else None
    if old_verdict == PHASE4_INTAKE_VERDICT and old_confidence == PHASE4_INTAKE_CONFIDENCE:
        return None
    return {
        "case_id": getattr(case, "case_id", ""),
        "old_verdict": old_verdict,
        "new_verdict": PHASE4_INTAKE_VERDICT,
        "old_confidence": old_confidence,
        "new_confidence": PHASE4_INTAKE_CONFIDENCE,
    }


def plan_state_normalization(
    resolved_questions: Iterable[str],
    evidence_raws: Iterable[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Change plan for one investigation state's resolved-questions set.

    Keeps only resolutions backed by substantive targeted evidence; questions
    resolved solely by analyst labels (or the old keyword heuristic's durable
    carry-over) reopen. This is deliberately conservative: an un-resolved
    question only adds a closure blocker until the next analysis re-derives
    it legitimately, while a stale resolution could wrongly permit closure.
    """
    old = [str(q).strip() for q in (resolved_questions or []) if str(q).strip()]
    backed = evidence_backed_questions(evidence_raws)
    kept = [q for q in old if q in backed]
    removed = [q for q in old if q not in backed]
    if not removed:
        return None
    return {"old_resolved": old, "kept_resolved": kept, "removed_resolved": removed}
