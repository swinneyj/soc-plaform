"""Phase 2 tests: per-card AI evidence verdicts (PHASE2_EVIDENCE_JSON).

Covers the three contract surfaces from the development plan:
  1. Prompt contract — the intro asks for the JSON block.
  2. Parser — valid/malformed/missing/bad-direction blocks.
  3. Scoring precedence — evidence_json > per_card text > global heuristic,
     with the verdict persisted into raw_result on the analyze flow.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.analysis_service import (  # noqa: E402
    build_analysis_prompt_intro,
    extract_phase2_evidence_json,
)
from services.investigation_state import (  # noqa: E402
    CONFIDENCE_HINT_DELTA,
    CONFIDENCE_HINT_MAX_IMPACT,
    _build_investigation_state,
    _serialize_investigation_state_record,
)


# ---------------------------------------------------------------- prompt
def test_prompt_contract_requests_evidence_json_block():
    intro = build_analysis_prompt_intro(has_prior_analysis=False)
    assert "PHASE2_EVIDENCE_JSON_START" in intro
    assert "PHASE2_EVIDENCE_JSON_END" in intro
    assert '"direction"' in intro
    assert "supports|refutes|neutral" in intro


# ---------------------------------------------------------------- parser
def test_parser_valid_block():
    text = (
        "PHASE2_EVIDENCE_JSON_START"
        '[{"title": "Encoded Payload", "direction": "supports", '
        '"rationale": "encoded cmd", "confidence_delta_hint": "increase"}]'
        "PHASE2_EVIDENCE_JSON_END"
    )
    out = extract_phase2_evidence_json(text)
    assert out == [{
        "title": "Encoded Payload",
        "direction": "supports",
        "rationale": "encoded cmd",
        "confidence_delta_hint": "increase",
    }]


def test_parser_missing_block_returns_empty():
    assert extract_phase2_evidence_json("no markers at all") == []


def test_parser_malformed_json_returns_empty():
    text = "PHASE2_EVIDENCE_JSON_START not json ] PHASE2_EVIDENCE_JSON_END"
    assert extract_phase2_evidence_json(text) == []


def test_parser_invalid_direction_dropped():
    text = (
        "PHASE2_EVIDENCE_JSON_START"
        '[{"title": "A", "direction": "maybe"},'
        '{"title": "B", "direction": "refutes", "rationale": "ok"}]'
        "PHASE2_EVIDENCE_JSON_END"
    )
    out = extract_phase2_evidence_json(text)
    assert [v["title"] for v in out] == ["B"]


def test_parser_unknown_hint_defaults_to_none():
    text = (
        "PHASE2_EVIDENCE_JSON_START"
        '[{"title": "A", "direction": "neutral", "confidence_delta_hint": "shrug"}]'
        "PHASE2_EVIDENCE_JSON_END"
    )
    assert extract_phase2_evidence_json(text)[0]["confidence_delta_hint"] == "none"


# ------------------------------------------------------------- precedence
def _make_case():
    class _Case:
        case_id = "TEST-X"
        rule_id = "r"
        rule_name = "Rule"
        verdict = "suspicious"
        confidence_score = 0.5
        analysis_summary = ""
    return _Case()


def _state(analysis_text, verdicts):
    evidence = [{
        "id": 1,
        "query_title": "Encoded Payload",
        "source_system": "splunk",
        "raw_result": {
            "result_text": "observed",
            "result_status": "success",
        },
        "created_at": "2026-09-28T00:00:00",
    }]
    return _build_investigation_state(
        _make_case(),
        analysis_text,
        [],
        evidence,
        "initial",
        {},
        evidence_verdicts=verdicts,
    )


def test_precedence_evidence_json_beats_text_and_heuristic():
    # The Per-Evidence Assessment text says refutes; the JSON block says
    # supports. JSON must win (it is the stricter contract).
    analysis = (
        "Initial Thoughts\nThe host is compromised.\n\n"
        "Per-Evidence Assessment\n"
        "[1] Encoded Payload — direction=refutes — text says otherwise\n\n"
        "PHASE2_EVIDENCE_JSON_START"
        '[{"title": "Encoded Payload", "direction": "supports", "rationale": "json rationale"}]'
        "PHASE2_EVIDENCE_JSON_END"
    )
    timeline = _state(analysis, extract_phase2_evidence_json(analysis))["evidence_summary"]["timeline"]
    entry = timeline[0]
    assert entry["finding_type"] == "supports"
    assert entry["ai_verdict_source"] == "evidence_json"
    assert entry["ai_verdict_rationale"] == "json rationale"


def test_precedence_falls_back_to_text_section_when_json_absent():
    analysis = (
        "Initial Thoughts\nSomething.\n\n"
        "Per-Evidence Assessment\n"
        "[1] Encoded Payload — direction=refutes — from text section\n"
    )
    timeline = _state(analysis, [])["evidence_summary"]["timeline"]
    entry = timeline[0]
    assert entry["finding_type"] == "refutes"
    assert entry["ai_verdict_source"] == "per_card"


def test_precedence_falls_back_to_global_heuristic_when_nothing_matches():
    analysis = (
        "Initial Thoughts\nThe encoded payload supports the malicious "
        "hypothesis strongly.\n"
    )
    timeline = _state(analysis, [])["evidence_summary"]["timeline"]
    entry = timeline[0]
    assert entry["ai_verdict_source"] == "analysis_text"
    assert entry["finding_type"] in {"supports", "refutes", "neutral"}


def test_unmatched_json_titles_are_ignored():
    analysis = (
        "Per-Evidence Assessment\n"
        "[1] Encoded Payload — direction=refutes — text wins\n"
        "PHASE2_EVIDENCE_JSON_START"
        '[{"title": "Totally Different Card", "direction": "supports"}]'
        "PHASE2_EVIDENCE_JSON_END"
    )
    timeline = _state(analysis, extract_phase2_evidence_json(analysis))["evidence_summary"]["timeline"]
    entry = timeline[0]
    assert entry["finding_type"] == "refutes"
    assert entry["ai_verdict_source"] == "per_card"


# ------------------------------------------------- confidence_delta_hint
def _state_multi(hints):
    """Build state from substantive evidence cards driven by structured
    verdicts carrying the given confidence_delta_hint values."""
    titles = ["Card One", "Card Two", "Card Three", "Card Four", "Card Five"][: max(len(hints), 2)]
    evidence = [
        {
            "id": idx + 1,
            "query_title": title,
            "source_system": "splunk",
            "raw_result": {"result_text": "observed", "result_status": "success"},
            "created_at": "2026-09-28T00:00:0%d" % idx,
        }
        for idx, title in enumerate(titles)
    ]
    verdicts = [
        {"title": title, "direction": "supports", "rationale": "r", "confidence_delta_hint": hint}
        for title, hint in zip(titles, hints)
    ]
    return _build_investigation_state(
        _make_case(),
        "Initial Thoughts\nSomething.",
        [],
        evidence,
        "initial",
        {},
        evidence_verdicts=verdicts,
    )


def test_hint_recorded_on_timeline_and_defaults_to_none_for_text_verdicts():
    state = _state_multi(["increase", "none"])
    timeline = state["evidence_summary"]["timeline"]
    hints = {entry["title"]: entry["confidence_delta_hint"] for entry in timeline}
    assert hints["Card One"] == "increase"
    assert hints["Card Two"] == "none"


def test_text_section_verdicts_never_carry_hints():
    analysis = (
        "Initial Thoughts\nSomething.\n\n"
        "Per-Evidence Assessment\n"
        "[1] Encoded Payload — direction=supports — from text section\n"
    )
    entry = _state(analysis, [])["evidence_summary"]["timeline"][0]
    assert entry["ai_verdict_source"] == "per_card"
    assert entry["confidence_delta_hint"] == "none"


def test_increase_hints_raise_confidence():
    baseline = _state_multi(["none", "none"])["disposition_confidence"]
    boosted = _state_multi(["increase", "increase"])["disposition_confidence"]
    assert boosted == round(baseline + 2 * CONFIDENCE_HINT_DELTA, 3)


def test_decrease_hints_lower_confidence():
    baseline = _state_multi(["none", "none"])["disposition_confidence"]
    lowered = _state_multi(["decrease", "decrease"])["disposition_confidence"]
    assert lowered == round(baseline - 2 * CONFIDENCE_HINT_DELTA, 3)


def test_mixed_hints_cancel_out():
    baseline = _state_multi(["none", "none"])["disposition_confidence"]
    mixed = _state_multi(["increase", "decrease"])["disposition_confidence"]
    assert mixed == baseline


def test_hint_impact_is_capped_in_aggregate():
    four_hints = _state_multi(["increase"] * 4)["disposition_confidence"]
    five_hints = _state_multi(["increase"] * 5)["disposition_confidence"]
    assert five_hints == four_hints  # 4*0.02 already exceeds the +0.06 cap
    baseline = _state_multi(["none"] * 5)["disposition_confidence"]
    assert four_hints == round(baseline + CONFIDENCE_HINT_MAX_IMPACT, 3)


def test_serializer_exposes_confidence_hints():
    record = SimpleNamespace(
        case_id="TEST-X",
        rule_id="r",
        current_hypothesis="h",
        provisional_disposition="malicious",
        disposition_confidence=0.66,
        loop_status="ready_for_disposition_review",
        iteration_count=2,
        unresolved_questions=json.dumps([]),
        closure_blockers=json.dumps([]),
        recommended_next_actions=json.dumps([]),
        evidence_summary=json.dumps({"timeline": [
            {"title": "A", "confidence_delta_hint": "increase"},
            {"title": "B", "confidence_delta_hint": "decrease"},
            {"title": "C", "confidence_delta_hint": "none"},
        ]}),
        last_analysis_stage="initial",
        updated_at=datetime(2026, 9, 28),
    )
    out = _serialize_investigation_state_record(record)
    assert out["confidence_hints"] == {"increase": 1, "decrease": 1}
