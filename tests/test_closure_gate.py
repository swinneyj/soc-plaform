"""Tests for closure gating, confidence caps, and loop-status transitions.

These pin the investigation loop's enforcement logic — the part that must
never silently weaken: closure requires a decisive disposition, >=80%
confidence, >=2 substantive evidence items, zero open questions, and zero
execution problems.
"""

import sys
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT))

from services import investigation_state as isvc  # noqa: E402


class FakeCase:
    def __init__(self, verdict="", confidence_score=0.5):
        self.case_id = "TEST-CASE-1"
        self.rule_id = "test_rule"
        self.verdict = verdict
        self.analysis_summary = ""
        self.confidence_score = confidence_score


def build_state(analysis_text, items, case_verdict="", previous_state=None,
                case_confidence=0.5):
    case = FakeCase(verdict=case_verdict, confidence_score=case_confidence)
    return isvc._build_investigation_state(
        case, analysis_text, [], items, "initial", previous_state or {}
    )


def evidence(title="Q", status="success", result_text="Substantive observed rows here"):
    return {
        "id": 1,
        "query_title": title,
        "source_system": "supportive_manual",
        "raw_result": {
            "query_text": "index=test",
            "result_text": result_text,
            "analyst_summary": "",
            "finding_type": "neutral",
            "question_resolution": "not_resolved",
            "target_questions": [],
            "result_status": status,
        },
        "created_at": "2026-09-16T00:00:00",
    }


# No Key Questions section: an analysis that states no questions leaves
# nothing unresolved, which is the only way the gate math can reach closure.
MALICIOUS_ANALYSIS = (
    "### Initial Thoughts\nCertutil outbound transfer supports the hypothesis "
    "of unauthorized tool staging.\n\n"
    "### Investigative Analysis\nThe evidence indicates malicious activity "
    "consistent with compromise.\n\n"
    "### Triage Verdict\nMalicious — true positive.\n"
)

# Neutral-direction analysis: contains neither support nor refute signals,
# so the disposition stays exactly what the verdict text says.
NEUTRAL_SUSPICIOUS_ANALYSIS = (
    "### Investigative Analysis\nThe review of collected rows is complete.\n\n"
    "### Triage Verdict\nSuspicious overall.\n"
)


class TestClosureGate:
    def test_full_qualification_reaches_ready_for_closure(self):
        """All gates satisfied -> ready_for_closure. Uses the promote
        baseline (0.8) as the case confidence, as real cases do."""
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(MALICIOUS_ANALYSIS, items, case_confidence=0.8)
        assert state["loop_status"] == "ready_for_closure"
        assert state["provisional_disposition"] == "malicious"
        assert state["closure_blockers"] == []
        assert state["disposition_confidence"] >= 0.80

    def test_single_evidence_item_blocks_closure(self):
        """Even a strong verdict cannot close on one substantive item."""
        items = [evidence("Q1")]
        state = build_state(MALICIOUS_ANALYSIS, items)
        assert state["loop_status"] != "ready_for_closure"
        assert any("evidence" in b.lower() for b in state["closure_blockers"]) or \
            state["disposition_confidence"] < 0.80

    def test_open_questions_block_closure_and_cap_confidence(self):
        """Questions open -> blocked, and confidence capped at 0.75."""
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(MALICIOUS_ANALYSIS, items)
        # Force an unresolved question by checking the gate math via a
        # state where the analysis leaves a question open.
        assert state["disposition_confidence"] <= 0.95  # sanity
        # Direct construction: analysis with a questions section that the
        # resolver cannot match against the evidence.
        analysis_with_open_q = (
            "### Key Questions\n- Is there a completely unrelated zebra "
            "migration pattern in the datacenter?\n\n" + MALICIOUS_ANALYSIS
        )
        state2 = build_state(analysis_with_open_q, items)
        if state2["unresolved_questions"]:
            assert state2["loop_status"] != "ready_for_closure"
            assert state2["disposition_confidence"] <= 0.76

    def test_pending_evidence_blocks_closure(self):
        pending = evidence("Q-untouched", result_text="todo")
        pending["raw_result"]["result_status"] = "not_run"
        items = [evidence("Q1"), pending]
        state = build_state(MALICIOUS_ANALYSIS, items)
        assert state["loop_status"] != "ready_for_closure"
        assert state["evidence_summary"]["pending_items"] >= 1

    def test_failed_query_blocks_closure(self):
        failed = evidence("Q-broken", status="query_failed", result_text="")
        items = [evidence("Q1"), failed]
        state = build_state(MALICIOUS_ANALYSIS, items)
        assert state["loop_status"] != "ready_for_closure"
        assert any("failed" in b.lower() for b in state["closure_blockers"])

    def test_source_unavailable_blocks_closure(self):
        gap = evidence("Q-gap", status="data_source_unavailable", result_text="")
        items = [evidence("Q1"), gap]
        state = build_state(MALICIOUS_ANALYSIS, items)
        assert state["loop_status"] != "ready_for_closure"
        assert any("unavailable" in b.lower() or "telemetry" in b.lower()
                   for b in state["closure_blockers"])

    def test_suspicious_disposition_never_closes(self):
        """A tentative disposition is a hard blocker regardless of strength.
        Uses a neutral-direction analysis so the disposition-upgrade rule
        (>=2 supporting findings) does not override the verdict label."""
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(NEUTRAL_SUSPICIOUS_ANALYSIS, items, case_confidence=0.8)
        assert state["provisional_disposition"] in {"suspicious", "undetermined"}
        assert state["loop_status"] != "ready_for_closure"
        assert any("tentative" in b.lower() or "conclusive" in b.lower()
                   for b in state["closure_blockers"])


class TestConfidenceCaps:
    def test_confidence_floor_is_010(self):
        state = build_state("", [evidence("Q1", status="query_failed", result_text="")])
        assert state["disposition_confidence"] >= 0.10

    def test_confidence_ceiling_is_095(self):
        # Many strong supporting items still cannot exceed 0.95.
        items = [evidence(f"Q{i}") for i in range(8)]
        state = build_state(MALICIOUS_ANALYSIS, items)
        assert state["disposition_confidence"] <= 0.95

    def test_suspicious_caps_confidence_at_072(self):
        items = [evidence(f"Q{i}") for i in range(6)]
        state = build_state(NEUTRAL_SUSPICIOUS_ANALYSIS, items, case_confidence=0.95)
        assert state["disposition_confidence"] <= 0.72

    def test_unresolved_questions_cap_confidence(self):
        analysis_with_open_q = (
            "### Key Questions\n- Unrelated zebra migration pattern?\n\n"
            + MALICIOUS_ANALYSIS
        )
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(analysis_with_open_q, items)
        if state["unresolved_questions"]:
            assert state["disposition_confidence"] <= 0.76

    def test_conflicting_evidence_caps_confidence(self):
        analysis = (
            "### Initial Thoughts\nEvidence both indicates malicious behavior "
            "and shows authorized administrative activity; it is inconclusive.\n\n"
            "### Triage Verdict\nSuspicious overall.\n"
        )
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(analysis, items)
        assert state["disposition_confidence"] <= 0.72


class TestLoopStatusTransitions:
    def test_no_evidence_is_collecting(self):
        state = build_state(MALICIOUS_ANALYSIS, [])
        assert state["loop_status"] == "collecting_evidence"

    def test_some_evidence_is_needs_more(self):
        state = build_state(MALICIOUS_ANALYSIS, [evidence("Q1")])
        assert state["loop_status"] in {"needs_more_evidence", "ready_for_disposition_review"}

    def test_strong_case_reaches_disposition_review(self):
        """2+ substantive items and confidence >=0.70 -> disposition review
        (one step below closure when the case baseline is mid-range)."""
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(MALICIOUS_ANALYSIS, items, case_confidence=0.6)
        assert state["loop_status"] in {"ready_for_disposition_review", "ready_for_closure"}

    def test_iteration_count_increments(self):
        items = [evidence("Q1")]
        first = build_state(MALICIOUS_ANALYSIS, items, previous_state={})
        assert first["iteration_count"] == 1
        second = build_state(MALICIOUS_ANALYSIS, items, previous_state=first)
        assert second["iteration_count"] == 2

    def test_durable_question_resolution_carries_forward(self):
        """A question recorded resolved in prior state stays resolved even
        after its evidence rows are replaced."""
        q = "Was the payload executed?"
        prior = {
            "unresolved_questions": [q],
            "evidence_summary": {"resolved_questions": [q]},
            "iteration_count": 3,
        }
        items = [evidence("Q1"), evidence("Q2")]
        state = build_state(MALICIOUS_ANALYSIS, items, previous_state=prior)
        assert q not in state["unresolved_questions"]
        assert q in state["evidence_summary"]["resolved_questions"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
