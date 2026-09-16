"""Regression tests for the investigation-loop fixes (commit cab4624).

Covers three areas that previously regressed silently:
1. Evidence ledger entry validation (api/main.py) — failure-status entries
   with empty result bodies must be saved, not dropped.
2. AI-derived direction scoring (services/investigation_state.py) — the
   analyst never sets direction; the model's analysis text decides, and the
   legacy "benign_result" verdict-status is neutralized.
3. Phase 3+ query generation (api/main.py) — question-driven variant cards
   must appear even when every playbook template has already been run.

All tests are pure unit tests: no database, no HTTP, no Ollama.
"""

import sys
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT))

import api.main as api_main  # noqa: E402
from services import investigation_state as isvc  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------

class FakeEntry:
    """Duck-typed InvestigationEvidenceEntryPayload."""

    def __init__(self, title="", result_text="", analyst_summary="",
                 query_text="", result_status="success"):
        self.query_title = title
        self.result_text = result_text
        self.analyst_summary = analyst_summary
        self.query_text = query_text
        self.result_status = result_status


class FakeDef:
    """Duck-typed supportive query definition (ORM-like)."""

    def __init__(self, title, spl, description=""):
        self.title = title
        self.spl_query = spl
        self.description = description


class FakeCase:
    """Minimal stand-in for a TriageResult row."""

    def __init__(self, verdict=""):
        self.case_id = "TEST-CASE-1"
        self.rule_id = "test_rule"
        self.verdict = verdict
        self.analysis_summary = ""
        self.confidence_score = 0.5


def evidence_item(title, status="success", result_text="some rows",
                  finding_type="neutral", target_questions=None):
    return {
        "id": 1,
        "query_title": title,
        "source_system": "supportive_manual",
        "raw_result": {
            "query_text": "index=test",
            "result_text": result_text,
            "analyst_summary": "",
            "finding_type": finding_type,
            "question_resolution": "not_resolved",
            "target_questions": target_questions or [],
            "result_status": status,
        },
        "created_at": "2026-09-16T00:00:00",
    }


# ---------------------------------------------------------------------------
# 1. Evidence ledger entry validation
# ---------------------------------------------------------------------------

class TestEvidenceEntryValidation:

    def test_no_results_with_empty_body_is_valid(self):
        """The core Stage-2 bug: a legitimate 0-event query must save."""
        entry = FakeEntry(title="Q1", result_text="", result_status="no_results")
        assert api_main._evidence_entry_is_valid(entry) is True

    def test_query_failed_with_empty_body_is_valid(self):
        entry = FakeEntry(title="Q1", result_text="", result_status="query_failed")
        assert api_main._evidence_entry_is_valid(entry) is True

    def test_data_source_unavailable_with_empty_body_is_valid(self):
        entry = FakeEntry(title="Q1", result_text="",
                          result_status="data_source_unavailable")
        assert api_main._evidence_entry_is_valid(entry) is True

    def test_blank_success_is_skipped(self):
        """An untouched success card is not evidence and stays skipped."""
        entry = FakeEntry(title="Q1", result_text="", result_status="success")
        assert api_main._evidence_entry_is_valid(entry) is False

    def test_success_with_result_text_is_valid(self):
        entry = FakeEntry(title="Q1", result_text="events here")
        assert api_main._evidence_entry_is_valid(entry) is True

    def test_success_with_query_text_but_no_result_is_valid(self):
        """Query text alone identifies the entry; keep it."""
        entry = FakeEntry(title="Q1", query_text="index=test | head")
        assert api_main._evidence_entry_is_valid(entry) is True

    def test_untitled_entry_is_always_skipped(self):
        entry = FakeEntry(title="", result_text="events")
        assert api_main._evidence_entry_is_valid(entry) is False

    def test_legacy_benign_result_status_maps_to_success(self):
        """'benign_result' is an analyst verdict; the ledger must not treat
        it as a distinct status anymore."""
        entry = FakeEntry(title="Q1", result_text="backup jobs",
                          result_status="benign_result")
        # Valid to save (has content) — the status normalization itself is
        # covered by the scoring tests below.
        assert api_main._evidence_entry_is_valid(entry) is True


# ---------------------------------------------------------------------------
# 2. AI-derived direction scoring
# ---------------------------------------------------------------------------

def build_state(analysis_text, items, verdict=""):
    case = FakeCase(verdict=verdict)
    return isvc._build_investigation_state(
        case, analysis_text, [], items, "initial", {}
    )


class TestAIDerivedDirection:

    def test_supporting_analysis_scores_supports(self):
        """Evidence entered with NO direction is scored from the AI text."""
        analysis = (
            "### Initial Thoughts\nThe certutil execution supports the "
            "hypothesis of unauthorized tool transfer.\n\n"
            "### Key Questions\n- Is the destination known-bad?\n\n"
            "### Investigative Analysis\nThe process indicates malicious "
            "behavior consistent with compromise.\n\n"
            "### Triage Verdict\nMalicious.\n"
        )
        items = [evidence_item("Q1", status="success")]
        state = build_state(analysis, items)
        by_finding = state["evidence_summary"]["by_finding"]
        assert by_finding["supports"] == 1
        assert by_finding["refutes"] == 0

    def test_refuting_analysis_scores_refutes(self):
        analysis = (
            "### Initial Thoughts\nActivity appears routine.\n\n"
            "### Key Questions\n- Was there a change ticket?\n\n"
            "### Investigative Analysis\nNo evidence of compromise; this is "
            "authorized administrative activity and a false positive.\n\n"
            "### Triage Verdict\nBenign.\n"
        )
        items = [evidence_item("Q1", status="success")]
        state = build_state(analysis, items)
        by_finding = state["evidence_summary"]["by_finding"]
        assert by_finding["refutes"] >= 1

    def test_analyst_finding_type_is_ignored(self):
        """Even if a legacy row says 'supports', a refuting AI analysis
        wins — the analyst never sets direction."""
        analysis = (
            "### Triage Verdict\nBenign. No evidence of malicious activity; "
            "authorized administrative behavior.\n"
        )
        items = [evidence_item("Q1", status="success", finding_type="supports")]
        state = build_state(analysis, items)
        by_finding = state["evidence_summary"]["by_finding"]
        assert by_finding["supports"] == 0
        assert by_finding["refutes"] >= 1

    def test_legacy_benign_result_status_neutralized(self):
        """Old rows stored as 'benign_result' must not add refute strength
        by status alone; they read as plain success."""
        analysis = (
            "### Triage Verdict\nUndetermined.\n"
        )
        items = [evidence_item("Q1", status="benign_result")]
        state = build_state(analysis, items)
        by_status = state["evidence_summary"]["by_status"]
        # The status is counted as plain success — the benign verdict key is gone
        assert by_status.get("benign_result", 0) == 0
        assert by_status["success"] == 1
        by_finding = state["evidence_summary"]["by_finding"]
        # No direction in the analysis text -> neutral, not refuted-by-verdict
        assert by_finding["refutes"] == 0

    def test_no_results_with_negative_ai_read_counts_as_substantive(self):
        analysis = (
            "### Investigative Analysis\nNo evidence of lateral movement "
            "was found; this refutes the hypothesis of compromise.\n"
        )
        items = [evidence_item("Q1", status="no_results", result_text="")]
        state = build_state(analysis, items)
        assert state["evidence_summary"]["substantive_items"] >= 1

    def test_direction_inference_defaults_to_neutral(self):
        assert isvc._infer_direction_from_analysis("") == "neutral"
        assert isvc._infer_direction_from_analysis("The weather is nice.") == "neutral"


# ---------------------------------------------------------------------------
# 3. Phase 3+ question-driven query generation
# ---------------------------------------------------------------------------

TEMPLATE_A = FakeDef(
    "Process execution check",
    'index=Windows host="$host$" certutil.exe',
    "Check process execution on the host.",
)
TEMPLATE_B = FakeDef(
    "Outbound destination check",
    'index=firewall dest="$dest$"',
    "Check destination reputation.",
)

STATE_WITH_QUESTIONS = {
    "unresolved_questions": [
        "What is the purpose of the certutil.exe execution?",
        "Is the destination host a known suspicious entity?",
    ],
    "closure_blockers": ["2 investigative question(s) remain unresolved."],
}


class TestPhaseFollowUpGeneration:

    def test_unused_templates_preferred_in_later_phase(self):
        # "Outbound destination check" already has saved evidence
        results = [evidence_item("Outbound destination check")]
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], STATE_WITH_QUESTIONS,
            results, phase_number=3,
        )
        titles = [c["title"] for c in cards]
        assert "Process execution check" in titles
        # The already-run template must not be re-surfaced while unused ones exist
        assert "Outbound destination check" not in titles

    def test_variants_generated_when_all_templates_run(self):
        """The Phase 3+ dead end: every template run, questions still open.
        Must return question-targeted variant cards, never an empty list."""
        results = [
            evidence_item("Process execution check"),
            evidence_item("Outbound destination check"),
        ]
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], STATE_WITH_QUESTIONS,
            results, phase_number=4,
        )
        assert len(cards) > 0
        for card in cards:
            assert card.get("is_variant") is True
            assert card["title"].startswith("Phase 4:")
            assert card.get("target_questions"), "variant must carry its question"
            # Variants are grounded: SPL comes from a real template
            assert card["spl"] in {TEMPLATE_A.spl_query, TEMPLATE_B.spl_query}

    def test_variant_targets_match_open_questions(self):
        results = [evidence_item("Process execution check"),
                   evidence_item("Outbound destination check")]
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], STATE_WITH_QUESTIONS,
            results, phase_number=5,
        )
        targeted = {t for c in cards for t in c.get("target_questions", [])}
        for question in STATE_WITH_QUESTIONS["unresolved_questions"]:
            assert question in targeted

    def test_no_questions_and_no_unused_templates_means_no_cards(self):
        """Only when nothing is open AND everything ran is an empty list
        acceptable."""
        results = [evidence_item("Process execution check"),
                   evidence_item("Outbound destination check")]
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], {"unresolved_questions": [], "closure_blockers": []},
            results, phase_number=4,
        )
        assert cards == []

    def test_phase_2_returns_empty_from_this_builder(self):
        """Phase 2 uses the classic fallback path, not this builder."""
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], STATE_WITH_QUESTIONS, [], phase_number=2,
        )
        assert cards == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
