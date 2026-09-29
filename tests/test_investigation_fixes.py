"""Regression tests for the investigation-loop fixes (commit cab4624).

Covers three areas that previously regressed silently:
1. Evidence ledger entry validation (api/main.py) — failure-status entries
   with empty result bodies must be saved, not dropped.
2. AI-derived direction scoring (services/investigation_state.py) — the
   analyst never sets direction; the model's analysis text decides, the
   legacy "benign_result" verdict-status is neutralized, and analyst
   labels (finding_type / question_resolution) plus the case row's stored
   pre-loop verdict are advisory only (Phase 4).
3. Phase 3+ query generation (api/main.py) — question-driven variant cards
   must appear even when every playbook template has already been run.

All tests are pure unit tests: no database, no HTTP, no Ollama.
"""

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT))

import api.main as api_main  # noqa: E402
import services.evidence_service as esvc  # noqa: E402
import services.judgment_normalization as jn  # noqa: E402
import services.retention_service as rsvc  # noqa: E402
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
    """Minimal stand-in for a TriageResult row.

    ``verdict`` is the pre-loop stored verdict (promote/seed time). Since
    Phase 4 it is advisory context: the state build never reads it.
    """

    def __init__(self, verdict="", confidence_score=0.5):
        self.case_id = "TEST-CASE-1"
        self.rule_id = "test_rule"
        self.verdict = verdict
        self.analysis_summary = ""
        self.confidence_score = confidence_score


def evidence_item(title, status="success", result_text="some rows",
                  finding_type="neutral", target_questions=None,
                  question_resolution="not_resolved"):
    return {
        "id": 1,
        "query_title": title,
        "source_system": "supportive_manual",
        "raw_result": {
            "query_text": "index=test",
            "result_text": result_text,
            "analyst_summary": "",
            "finding_type": finding_type,
            "question_resolution": question_resolution,
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

def build_state(analysis_text, items, verdict="", previous_state=None,
                case_confidence=0.5):
    case = FakeCase(verdict=verdict, confidence_score=case_confidence)
    return isvc._build_investigation_state(
        case, analysis_text, [], items, "initial", previous_state or {}
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
            # Variants are grounded: SPL comes from a real template, with a
            # phase-scoped time re-scope so each phase is a genuinely new query.
            assert any(t.spl_query in card["spl"] for t in (TEMPLATE_A, TEMPLATE_B))
            assert f"earliest=-{4}h" in card["spl"]

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

    def test_no_cards_only_when_loop_is_done(self):
        """Empty is correct only when the loop has converged: nothing open AND
        closure-ready. (This test previously pinned the empty-cards dead end
        for any exhausted playbook — the SPL phase-degradation bug.)"""
        results = [evidence_item("Process execution check"),
                   evidence_item("Outbound destination check")]
        done_state = {
            "unresolved_questions": [],
            "closure_blockers": [],
            "loop_status": "ready_for_closure",
        }
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], done_state,
            results, phase_number=4,
        )
        assert cards == []

    def test_exhausted_playbook_still_yields_verification_rechecks(self):
        """No questions, no blockers, loop still needs work -> keep offering
        fresh re-scoped re-checks instead of dead-ending (DEVELOPMENT_PLAN §8)."""
        results = [evidence_item("Process execution check"),
                   evidence_item("Outbound destination check")]
        state = {
            "unresolved_questions": [],
            "closure_blockers": [],
            "loop_status": "needs_more_evidence",
            "current_hypothesis": "Staging activity on the impacted host.",
        }
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], state,
            results, phase_number=4,
        )
        assert cards
        for card in cards:
            assert card.get("is_variant") is True
            assert card.get("target_questions")
        assert any(
            "Staging activity" in t
            for card in cards for t in card["target_questions"]
        )

    def test_current_response_questions_become_targets_immediately(self):
        """Questions the current analysis just raised are open work NOW."""
        results = [evidence_item("Process execution check"),
                   evidence_item("Outbound destination check")]
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B],
            {"unresolved_questions": [], "closure_blockers": []},
            results, phase_number=3,
            current_questions=["Was lateral movement observed?"],
        )
        assert cards
        targeted = {t for c in cards for t in c.get("target_questions", [])}
        assert "Was lateral movement observed?" in targeted

    def test_phase_2_returns_empty_from_this_builder(self):
        """Phase 2 uses the classic fallback path, not this builder."""
        cards = api_main._build_question_driven_followup_queries(
            [TEMPLATE_A, TEMPLATE_B], STATE_WITH_QUESTIONS, [], phase_number=2,
        )
        assert cards == []


# ---------------------------------------------------------------------------
# 4. Ollama model tag resolution
# ---------------------------------------------------------------------------

class TestTargetedEvidenceResolution:
    """Evidence-targeted question resolution (live gap 2026-09-29).

    Auto-collected splunk_auto rows never carry an analyst
    question_resolution click, so inquiries could only resolve via a brittle
    keyword heuristic — real rows say "geo=Bucharest", never "location", and
    the loop could never converge. A substantive row executed against
    specific inquiries (target_questions) now resolves them; the model
    re-raises anything it still doubts in the next phase's Key Questions.
    """

    QUESTION = (
        "Can we verify the user's location and IP address at the time of "
        "the login attempts?"
    )
    ANALYSIS = (
        "### Initial Thoughts\nVPN geo-velocity violation for user bjones.\n\n"
        "### Key Questions\n- " + QUESTION + "\n\n"
        "### Investigative Analysis\nGeo evidence is under review.\n\n"
        "### Triage Verdict\nSuspicious.\n"
    )
    # Result text deliberately free of the question's own keywords so only
    # the targeting linkage can resolve it (the keyword heuristic cannot).
    ROW = "src_ip=10.20.30.77 geo=Bucharest app=ssh"

    def test_substantive_targeted_row_resolves_question(self):
        items = [evidence_item(
            "Successful logins by user", status="success", result_text=self.ROW,
            target_questions=[self.QUESTION],
        )]
        state = build_state(self.ANALYSIS, items)
        assert self.QUESTION not in state["unresolved_questions"]
        assert self.QUESTION in state["evidence_summary"]["resolved_questions"]

    def test_failed_targeted_row_keeps_question_open(self):
        items = [evidence_item(
            "Geo lookup", status="query_failed", result_text="",
            target_questions=[self.QUESTION],
        )]
        state = build_state(self.ANALYSIS, items)
        assert self.QUESTION in state["unresolved_questions"]

    def test_untargeted_substantive_row_does_not_resolve(self):
        items = [evidence_item(
            "Unrelated check", status="success", result_text=self.ROW,
        )]
        state = build_state(self.ANALYSIS, items)
        assert self.QUESTION in state["unresolved_questions"]

    def test_resolution_survives_row_replacement(self):
        items = [evidence_item(
            "Successful logins by user", status="success", result_text=self.ROW,
            target_questions=[self.QUESTION],
        )]
        first = build_state(self.ANALYSIS, items)
        # Re-saving evidence replaces rows; a rebuild with the replaced (empty)
        # ledger must keep the resolution via the durable carry-over.
        second = build_state(self.ANALYSIS, [], previous_state=first)
        assert self.QUESTION not in second["unresolved_questions"]


class TestAdvisoryAnalystLabels:
    """Phase 4 judgment-flow audit: analyst labels never decide outcomes.

    ``finding_type`` and ``question_resolution`` are stored for the audit
    trail only — direction and inquiry resolution are derived (model
    verdicts + substantive targeted evidence). The case row's pre-loop
    stored verdict (promote/seed time) is likewise never a disposition
    fallback, so a referring analyst's ES disposition cannot leak into the
    platform's conclusions.
    """

    def test_analyst_question_resolution_label_cannot_resolve(self):
        # Pre-Phase-4 regression: a row the analyst marked "resolved" pulled
        # its target_questions out of the ledger even when the row itself
        # never produced evidence. The label is now inert.
        q = "Was unauthorized tool staging observed?"
        analysis = "### Key Questions\n- " + q + "\n\n### Triage Verdict\nSuspicious.\n"
        items = [evidence_item(
            "Pending sweep", status="not_run", result_text="",
            target_questions=[q], question_resolution="resolved",
        )]
        state = build_state(analysis, items)
        assert q in state["unresolved_questions"]
        assert q not in state["evidence_summary"]["resolved_questions"]

    def test_legacy_resolved_label_rows_still_round_trip(self):
        # Pre-Phase-4 rows carry resolution labels; they must keep parsing
        # and serializing through the timeline without special-casing.
        items = [evidence_item("Legacy row", question_resolution="partially_resolved")]
        state = build_state("### Triage Verdict\nSuspicious.\n", items)
        entry = state["evidence_summary"]["timeline"][0]
        assert entry["result_status"] == "success"

    def test_stored_verdict_is_not_a_disposition_fallback(self):
        # An empty model verdict section must fall through to "undetermined",
        # never inherit the referring analyst's stored conclusion.
        analysis = "### Initial Thoughts\nSomething benign.\n"
        state = build_state(analysis, [], verdict="malicious")
        assert state["provisional_disposition"] == "undetermined"

    def test_supporting_evidence_upgrades_benign_tracked_case(self):
        # Pre-Phase-4 regression: a case whose stored verdict said "benign"
        # could never be upgraded to malicious (the benign_tracked guard).
        # Stored verdicts no longer guard or pin — the ledger decides.
        analysis = (
            "### Investigative Analysis\nThe evidence indicates malicious "
            "activity consistent with compromise.\n\n"
            "### Triage Verdict\nSuspicious.\n"
        )
        items = [
            evidence_item("Q1", finding_type="neutral"),
            evidence_item("Q2", finding_type="neutral"),
        ]
        state = build_state(analysis, items, verdict="benign")
        assert state["provisional_disposition"] == "malicious"

    def test_refuting_evidence_yields_false_positive_regardless_of_stored_verdict(self):
        analysis = (
            "### Investigative Analysis\nNo evidence of compromise; this is "
            "authorized administrative activity and a false positive.\n\n"
            "### Triage Verdict\nBenign.\n"
        )
        items = [
            evidence_item("Q1", finding_type="neutral"),
            evidence_item("Q2", finding_type="neutral"),
        ]
        state = build_state(analysis, items, verdict="malicious")
        assert state["provisional_disposition"] == "false_positive"

    def test_promote_baseline_is_the_closure_gate_regardless_of_disposition(self):
        # Phase 4: the pasted ES disposition is no longer scored; every
        # promoted case starts at the 0.80 closure-gate baseline.
        assert api_main.derive_triage_confidence("") == 0.8
        assert api_main.derive_triage_confidence("Benign - true positive") == 0.8
        assert api_main.derive_triage_confidence("True Positive") == 0.8

    def test_derive_triage_verdict_is_gone(self):
        """The disposition->verdict mapper was the analyst-judgment leak;
        promoted verdicts are now fixed to "suspicious"."""
        assert not hasattr(api_main, "derive_triage_verdict")


class _FakeAnalysisRow:
    """Duck-typed AnalysisResult row for the pure retention selection."""

    def __init__(self, row_id, case_id, created_at):
        self.id = row_id
        self.case_id = case_id
        self.created_at = created_at


class TestAnalysisRetentionSelection:
    """Phase 5 retention policy (pure): keep the newest N analyses per case;
    closure-linked cases are permanent record and are never pruned."""

    def _rows(self, specs):
        return [_FakeAnalysisRow(*spec) for spec in specs]

    def test_keeps_newest_n_per_case(self):
        rows = self._rows([
            (1, "A", datetime(2026, 9, 1, 10)),
            (2, "A", datetime(2026, 9, 1, 11)),
            (3, "A", datetime(2026, 9, 1, 12)),
            (4, "A", datetime(2026, 9, 1, 13)),
        ])
        assert rsvc.select_prunable_analysis_ids(rows, keep_last_n=2) == [1, 2]

    def test_closure_linked_case_is_never_pruned(self):
        # B has two rows so that without the protection rule one of them
        # would fall under keep_last_n=1 — the rule must actually bite.
        rows = self._rows([
            (1, "A", datetime(2026, 9, 1, 10)),
            (2, "A", datetime(2026, 9, 1, 11)),
            (3, "A", datetime(2026, 9, 1, 12)),
            (4, "B", datetime(2026, 9, 1, 10)),
            (5, "B", datetime(2026, 9, 1, 11)),
        ])
        prunable = rsvc.select_prunable_analysis_ids(
            rows, keep_last_n=1, protected_case_ids={"B"}
        )
        assert prunable == [1, 2]

    def test_cases_are_independent(self):
        rows = self._rows([
            (1, "A", datetime(2026, 9, 1, 10)),
            (2, "A", datetime(2026, 9, 1, 11)),
            (3, "B", datetime(2026, 9, 1, 12)),
            (4, "B", datetime(2026, 9, 1, 13)),
        ])
        assert rsvc.select_prunable_analysis_ids(rows, keep_last_n=1) == [1, 3]

    def test_created_at_ties_break_by_id(self):
        rows = self._rows([
            (1, "A", datetime(2026, 9, 1, 10)),
            (2, "A", datetime(2026, 9, 1, 10)),
            (3, "A", datetime(2026, 9, 1, 10)),
        ])
        assert rsvc.select_prunable_analysis_ids(rows, keep_last_n=1) == [1, 2]

    def test_keep_zero_prunes_every_unprotected_row(self):
        rows = self._rows([
            (1, "A", datetime(2026, 9, 1, 10)),
            (2, "B", datetime(2026, 9, 1, 11)),
        ])
        assert rsvc.select_prunable_analysis_ids(rows, keep_last_n=0) == [1, 2]


class TestJudgmentNormalization:
    """Phase 4 data migration (pure): legacy judgment values normalize to
    what today's write paths produce, and question resolution keeps only
    evidence-backed entries."""

    def test_label_normalizes_to_not_resolved(self):
        assert jn.normalize_question_resolution_label("resolved") == "not_resolved"
        assert jn.normalize_question_resolution_label("partially_resolved") == "not_resolved"
        assert jn.normalize_question_resolution_label("") == "not_resolved"

    def test_raw_result_preserves_everything_but_the_label(self):
        raw = {
            "result_text": "rows here",
            "finding_type": "supports",
            "question_resolution": "resolved",
            "target_questions": ["Q?"],
        }
        out = jn.normalize_raw_result(raw)
        assert out["question_resolution"] == "not_resolved"
        assert out["result_text"] == "rows here"
        assert out["finding_type"] == "supports"
        assert out["target_questions"] == ["Q?"]
        assert raw["question_resolution"] == "resolved"  # input untouched

    def test_raw_result_without_label_gets_no_new_key(self):
        out = jn.normalize_raw_result({"result_text": "rows here"})
        assert "question_resolution" not in out

    def test_substantive_rule_mirrors_the_state_builder(self):
        assert jn.row_is_substantive({"result_status": "success", "result_text": "sshd rows observed"})
        assert not jn.row_is_substantive({"result_status": "success", "result_text": "todo"})
        assert not jn.row_is_substantive({"result_status": "not_run", "result_text": "sshd rows observed"})
        # The live builder treats every 0-row result as substantive — the
        # negative baseline "0 events returned" is itself an observation.
        assert jn.row_is_substantive({"result_status": "no_results"})
        assert jn.row_is_substantive({"result_status": "no_results", "ai_finding_type": "refutes"})

    def test_only_substantive_targeted_rows_back_questions(self):
        raws = [
            {"result_status": "success", "result_text": "sshd rows observed", "target_questions": ["Q1"]},
            {"result_status": "query_failed", "target_questions": ["Q2"]},
            {"result_status": "success", "result_text": "sshd rows observed", "target_questions": []},
        ]
        assert jn.evidence_backed_questions(raws) == {"Q1"}

    def test_triage_row_plan_only_when_nonconformant(self):
        class _Case:
            case_id = "X"
            verdict = "malicious"
            confidence_score = 0.6

        change = jn.plan_triage_normalization(_Case())
        assert change["old_verdict"] == "malicious"
        assert change["new_verdict"] == "suspicious"
        assert change["new_confidence"] == 0.8
        _Case.verdict = "suspicious"
        _Case.confidence_score = 0.8
        assert jn.plan_triage_normalization(_Case()) is None

    def test_state_plan_reopens_label_only_resolutions(self):
        raws = [
            {"result_status": "success", "result_text": "sshd rows observed", "target_questions": ["Q1"]},
        ]
        change = jn.plan_state_normalization(["Q1", "Q2", "Q3"], raws)
        assert change["kept_resolved"] == ["Q1"]
        assert change["removed_resolved"] == ["Q2", "Q3"]
        assert jn.plan_state_normalization(["Q1"], raws) is None


class TestModelResolution:
    def _client(self, installed):
        import services.ollama_service as osvc
        client = osvc.OllamaClient.__new__(osvc.OllamaClient)
        client.list_models = lambda: list(installed)
        return client

    def test_requested_tag_installed_returned_verbatim(self):
        client = self._client(["llama3.1:latest", "qwen:7b"])
        assert client.resolve_model("qwen:7b") == "qwen:7b"

    def test_uninstalled_requested_tag_falls_back_to_preference(self):
        """The first-run bug: request 'llama3.1:8b' when only :latest exists."""
        client = self._client(["llama3.1:latest"])
        assert client.resolve_model("llama3.1:8b") == "llama3.1:latest"

    def test_legacy_default_maps_to_installed_latest(self):
        client = self._client(["llama3.1:latest"])
        assert client.resolve_model("llama3.1:8b") == "llama3.1:latest"

    def test_no_request_uses_preference_order(self):
        client = self._client(["mistral", "llama3.1:latest"])
        assert client.resolve_model("") == "llama3.1:latest"

    def test_any_installed_model_used_when_no_preference_matches(self):
        client = self._client(["qwen2.5:14b"])
        assert client.resolve_model("") == "qwen2.5:14b"

    def test_nothing_installed_returns_requested_or_default(self):
        client = self._client([])
        assert client.resolve_model("llama3.1:8b") == "llama3.1:8b"
        assert client.resolve_model("").startswith("llama3.1")


# ---------------------------------------------------------------------------
# 5. Shared analysis prompt builders (single source of truth)
# ---------------------------------------------------------------------------

class TestSharedPromptBuilders:
    def test_intro_requests_three_sections_and_bans_spl(self):
        from services.analysis_service import build_analysis_prompt_intro
        intro = build_analysis_prompt_intro(has_prior_analysis=False)
        for token in ["1. Initial Thoughts", "2. Key Questions",
                      "3. Investigative Analysis", "Do not generate SPL"]:
            assert token in intro
        assert "PHASE2_QUERIES_JSON_START" not in intro

    def test_intro_prior_analysis_clause(self):
        from services.analysis_service import build_analysis_prompt_intro
        without = build_analysis_prompt_intro(False)
        with_prior = build_analysis_prompt_intro(True)
        assert "PREVIOUS ANALYSIS" not in without
        assert "PREVIOUS ANALYSIS" in with_prior

    def test_evidence_entries_include_status_exclude_direction(self):
        """The ledger block carries collection status but never an analyst
        direction — direction is AI-derived downstream."""
        from services.analysis_service import format_evidence_ledger_entries
        items = [evidence_item("Q1", status="no_results", result_text="")]
        blocks = format_evidence_ledger_entries(items)
        assert len(blocks) == 1
        assert "Collection Status: no_results" in blocks[0]
        assert "Direction" not in blocks[0]

    def test_evidence_entries_handle_non_dict_raw(self):
        from services.analysis_service import format_evidence_ledger_entries
        items = [{"query_title": "Q1", "source_system": "splunk",
                  "raw_result": "legacy string blob"}]
        blocks = format_evidence_ledger_entries(items)
        assert len(blocks) == 1
        assert "legacy string blob" in blocks[0]


def run_ui_harness_scenario(harness: Path, scenario: str) -> None:
    """Run one scenario of a node-vm UI regression harness (tests/ui_regression/).

    The harness loads the real web/modules/*.js into a node:vm sandbox with
    stubbed axios/window and canaries for window.alert / console.error. Any
    assertion failure exits non-zero with a FAIL line on stderr.
    """
    node = shutil.which("node")
    assert node, "node is required for the UI regression harness"
    proc = subprocess.run(
        [node, str(harness), scenario],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=PLATFORM_ROOT,
    )
    assert proc.returncode == 0, (
        f"scenario '{scenario}' failed (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
    assert "OK " + scenario in proc.stdout, f"unexpected harness output: {proc.stdout!r}"


class TestRunSplunkSearchInlineStatus:
    """UI regression (live bug 2026-09-29): runSplunkSearch must report status
    via the per-card inline chip (running -> complete | error) and must never
    call window.alert — modal alerts wedge embedded webviews mid-loop.

    Each test shells out to a zero-dependency node harness that loads the real
    web/modules/analysis.js into a vm sandbox with stubbed axios/window and
    drives the actual production method. Skipped when node is unavailable.
    """

    HARNESS = PLATFORM_ROOT / "tests" / "ui_regression" / "run_splunk_search_status.mjs"

    def _run_scenario(self, scenario: str) -> None:
        run_ui_harness_scenario(self.HARNESS, scenario)

    def test_success_chip_transitions_without_alert(self):
        self._run_scenario("success")

    def test_failure_chip_transitions_without_alert(self):
        self._run_scenario("failure")

    def test_no_case_selected_error_chip_without_alert(self):
        self._run_scenario("no_case")

    def test_empty_spl_error_chip_without_alert(self):
        self._run_scenario("empty_spl")

    def test_midflight_running_chip_observed(self):
        self._run_scenario("running_to_complete_transitions")


class TestUIEvidencePromoteLoadFlows:
    """UI regression harness for the evidence save / promote / saved-evidence
    load flows (live bug 2026-09-25, fixed in 6942458: loadSavedPhase2Evidence
    wrote to removed phase2Resolution* state keys and its catch block swallowed
    the crash into console.error, silently losing every saved-evidence load).

    Same zero-dependency node:vm approach as TestRunSplunkSearchInlineStatus:
    the real web/modules/analysis.js and web/modules/database.js run in a
    sandbox with stubbed axios and canaries for window.alert (blocking modals),
    console.error (swallowed crashes), and console.warn (guarded aborts). The
    load-path scenario is mutation-checked: restoring the crashy lines must
    fail it (hydration lost + canary fires).
    """

    HARNESS = PLATFORM_ROOT / "tests" / "ui_regression" / "evidence_promote_load.mjs"

    def _run(self, scenario: str) -> None:
        run_ui_harness_scenario(self.HARNESS, scenario)

    def test_supportive_save_payload_and_state_handoff(self):
        self._run("save_supportive")

    def test_phase2_save_payload_advisory_labels_and_edits(self):
        self._run("save_phase2")

    def test_busy_save_never_double_posts(self):
        self._run("save_busy_guard")

    def test_saved_evidence_load_survives_legacy_raw_result_keys(self):
        self._run("load_saved_evidence")

    def test_saved_evidence_load_failure_logs_without_throwing(self):
        self._run("load_saved_error")

    def test_saved_evidence_load_without_case_skips_network(self):
        self._run("load_saved_no_case")

    def test_promote_posts_refreshes_and_clears_busy_flag(self):
        self._run("promote")

    def test_promote_historical_guard_blocks_unpromoted(self):
        self._run("promote_historical_guard")

    def test_promote_failure_logs_without_alert_or_refresh(self):
        self._run("promote_failure")

    def test_bulk_promote_filters_continues_and_refreshes_once(self):
        self._run("promote_all_open")


class TestNotableParsing:
    """Direct coverage for services/evidence_service.py (was referenced by
    zero test files): the paste parsers, field normalization, entity
    extraction, and the Stage-1 parse assessment scoring."""

    def test_normalize_pasted_text_cleans_line_endings_and_invisibles(self):
        raw = "\ufeffTitle: X\u200b\r\nUser: bjones\rHost: srv1\r"
        out = esvc.normalize_pasted_text(raw)
        assert out == "Title: X\nUser: bjones\nHost: srv1"

    def test_parse_json_notable_stringifies_and_skips_nulls(self):
        raw = '{"user": "bjones", "count": 3, "none": null}'
        assert esvc.parse_json_notable(raw) == {"user": "bjones", "count": "3"}
        assert esvc.parse_json_notable("not json") == {}

    def test_parse_delimited_tsv_row(self):
        raw = "host\tuser\t_time\nsrv1\tbjones\t2026-09-25"
        assert esvc.parse_delimited_notable(raw) == {
            "host": "srv1", "user": "bjones", "_time": "2026-09-25",
        }

    def test_parse_delimited_rejects_unrecognized_headers(self):
        assert esvc.parse_delimited_notable("a,b,c\n1,2,3") == {}

    def test_parse_key_value_pairs_quoted_and_unquoted(self):
        raw = 'src_ip=10.0.0.5 user="bjones jones" app=ssh, action=success'
        parsed = esvc.parse_key_value_pairs(raw)
        assert parsed["src_ip"] == "10.0.0.5"
        assert parsed["user"] == "bjones jones"
        assert parsed["app"] == "ssh"
        assert parsed["action"] == "success"

    def test_parse_line_based_notable_skips_comments(self):
        raw = "User: bjones\n# internal note\nHost: srv1"
        assert esvc.parse_line_based_notable(raw) == {"User": "bjones", "Host": "srv1"}

    def test_comprehensive_merges_line_and_kv_with_normalization(self):
        raw = "User: bjones\nsrc_ip=10.0.0.5"
        fields = esvc.parse_pasted_notable_comprehensive(raw)
        assert fields["user"] == "bjones"
        assert fields["src_user"] == "bjones"  # cross-field sync
        assert fields["src_ip"] == "10.0.0.5"

    def test_normalize_aliases_and_cross_field_sync(self):
        fields = esvc.normalize_notable_fields({
            "Rule Name": "Impossible Travel",
            "host": "srv1",
            "user": "bjones",
            "source_ip": "10.0.0.5",
            "destination_ip": "10.0.0.9",
        })
        assert fields["correlation_search"] == "Impossible Travel"
        assert fields["destination"] == "srv1"
        assert fields["src_user"] == "bjones"
        assert fields["src_ip"] == "10.0.0.5"
        assert fields["dest_ip"] == "10.0.0.9"

    def test_extract_entities_respects_existing_fields(self):
        raw = "from 10.0.0.5 to 10.0.0.9 on web01.corp.internal"
        fields = esvc.extract_entities_from_raw(raw, {})
        assert fields["source_ip"] == "10.0.0.5"
        assert fields["destination_ip"] == "10.0.0.9"
        assert fields["host"].startswith("web01")
        assert fields["destination"] == fields["host"]
        # Existing values are never overwritten.
        kept = esvc.extract_entities_from_raw(raw, {"source_ip": "172.16.0.1"})
        assert kept["source_ip"] == "172.16.0.1"

    def test_parse_assessment_full_anchors_is_normal_mode(self):
        fields = {
            "host": "srv1", "user": "bjones", "source_ip": "10.0.0.5",
            "process": "sshd", "time": "2026-09-25T00:00:00",
        }
        assessment = esvc.build_parse_assessment(fields)
        assert assessment["score"] == 100
        assert assessment["mode"] == "normal"
        assert assessment["missing_anchors"] == []
        assert assessment["generic_queries"] == []

    def test_parse_assessment_empty_fields_is_extraction_mode(self):
        assessment = esvc.build_parse_assessment({})
        assert assessment["score"] == 40
        assert assessment["mode"] == "extraction"
        assert len(assessment["missing_anchors"]) == 5
        # Enrichment drafts carry unresolved placeholders for the analyst.
        assert "$host$" in assessment["generic_queries"][0]["spl"]

    def test_parse_assessment_partial_fields_is_enrichment_band(self):
        assessment = esvc.build_parse_assessment({"host": "srv1", "user": "bjones"})
        assert assessment["score"] == 70
        assert assessment["mode"] == "enrichment"
        assert assessment["missing_anchors"] == [
            "network IP", "process/executable", "event timestamp",
        ]


class TestSeedContractConformance:
    """Seeding must reproduce the Phase 4 intake contract.

    Documented debt (fixed Sept 29): re-seeding via seed_mock_splunk /
    seed_test_cases re-introduced legacy judgment semantics (per-scenario
    verdicts like benign/0.61). Both seeders now write the contract constants,
    and every seeded triage row must be invisible to the migration sweep —
    plan_triage_normalization(row) is None means "already conformant".
    """

    def _load_seed_mock_module(self):
        import importlib.util

        path = PLATFORM_ROOT / "scripts" / "seed_mock_splunk.py"
        spec = importlib.util.spec_from_file_location("seed_mock_splunk", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _fresh_session_factory(self, monkeypatch, seeder_module):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from db.models import Base

        engine = create_engine("sqlite://")
        Base.metadata.create_all(bind=engine)
        factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(seeder_module, "SessionLocal", factory)
        return factory

    def _assert_rows_conformant(self, factory, triage_model):
        from services import judgment_normalization as jn

        db = factory()
        try:
            rows = db.query(triage_model).all()
        finally:
            db.close()
        assert rows, "seeder produced no triage rows"
        for row in rows:
            assert jn.plan_triage_normalization(row) is None, (
                f"seeded row {row.case_id} carries legacy judgment "
                f"({row.verdict}/{row.confidence_score}); re-seeding would "
                f"reintroduce pre-Phase-4 semantics"
            )

    def test_seed_test_cases_rows_are_phase4_conformant(self, monkeypatch):
        import seed_test_cases as stc

        factory = self._fresh_session_factory(monkeypatch, stc)
        stc.seed_test_cases()
        self._assert_rows_conformant(factory, stc.TriageResult)

    def test_seed_mock_splunk_rows_are_phase4_conformant(self, monkeypatch):
        from services.search_backend import MockSplunkBackend

        seed_mock = self._load_seed_mock_module()
        factory = self._fresh_session_factory(monkeypatch, seed_mock)
        seed_mock.seed_db(MockSplunkBackend(), reset=False)
        self._assert_rows_conformant(factory, seed_mock.TriageResult)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
