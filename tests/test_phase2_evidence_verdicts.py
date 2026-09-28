"""Phase 2 tests: per-card AI evidence verdicts (PHASE2_EVIDENCE_JSON).

Covers the three contract surfaces from the development plan:
  1. Prompt contract — the intro asks for the JSON block.
  2. Parser — valid/malformed/missing/bad-direction blocks.
  3. Scoring precedence — evidence_json > per_card text > global heuristic,
     with the verdict persisted into raw_result on the analyze flow.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.analysis_service import (  # noqa: E402
    build_analysis_prompt_intro,
    extract_phase2_evidence_json,
)
from services.investigation_state import (  # noqa: E402
    _build_investigation_state,
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
