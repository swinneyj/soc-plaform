"""API-level tests for the full /db/analyze request path.

The unit suites (test_investigation_fixes, test_closure_gate) exercise the
scoring/gating logic in isolation. These tests drive the real FastAPI routes
through TestClient with two substitutions:

- Ollama is replaced by a fake client that reads the evidence titles out of
  the assembled prompt and emits a Per-Evidence Assessment for each one —
  mirroring what the real model is asked to do.
- db.models.SessionLocal is repointed at an in-memory SQLite database, so no
  live Postgres is needed and the test is hermetic.

Regression coverage: the per-card verdict persistence in /db/analyze once
mutated ORM rows that an earlier db.close() had detached — the commit
"succeeded" and the writes were silently lost. These tests fail on that code
because they read the verdicts back through a fresh session, exactly like
the UI does.
"""

import json
import sys
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

PLATFORM_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLATFORM_ROOT))

import db.models as db_models  # noqa: E402
import services.ollama_service as ollama_service  # noqa: E402
from api.main import app  # noqa: E402
import api.main as api_main  # noqa: E402
import services.search_backend as search_backend_mod  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Ollama client
# ---------------------------------------------------------------------------

class FakeOllamaClient:
    """Stands in for OllamaClient at the get_ollama_client() seam.

    generate() extracts the INVESTIGATION EVIDENCE block from the assembled
    prompt and returns an analysis whose Per-Evidence Assessment judges each
    numbered entry, alternating supports/refutes by index so tests can assert
    exact per-card outcomes.
    """

    def __init__(self):
        self.available = True
        self.model = "fake-model:latest"
        self.calls = []

    def generate(self, prompt, model=None, temperature=None, options=None):
        self.calls.append(prompt)
        titles = self._extract_evidence_titles(prompt)
        lines = []
        for idx, title in enumerate(titles, 1):
            direction = "supports" if idx % 2 == 1 else "refutes"
            lines.append(
                f"[{idx}] {title} — direction={direction} — Fake rationale for entry {idx}."
            )
        response = (
            "### Initial Thoughts\nThe evidence suggests staged activity.\n\n"
            "### Key Questions\n- Was the activity authorized?\n\n"
            "### Investigative Analysis\nThe collected rows indicate suspicious activity.\n\n"
            "### Per-Evidence Assessment\n" + "\n".join(lines) + "\n\n"
            "### Triage Verdict\nSuspicious overall.\n"
        )
        return {"success": True, "response": response, "model": model or self.model}

    @staticmethod
    def _extract_evidence_titles(prompt):
        titles = []
        in_block = False
        for line in prompt.splitlines():
            if "=== INVESTIGATION EVIDENCE ===" in line:
                in_block = True
                continue
            if in_block:
                stripped = line.strip()
                if stripped.startswith("==="):
                    break  # next prompt section begins
                if stripped.startswith("[") and "]" in stripped:
                    title = stripped[stripped.index("]") + 1:].split("(")[0].strip()
                    if title:
                        titles.append(title)
        return titles


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(monkeypatch):
    """TestClient with an isolated SQLite database and fake Ollama."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_models.Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    monkeypatch.setattr(db_models, "SessionLocal", test_session)
    fake = FakeOllamaClient()
    monkeypatch.setattr(ollama_service, "get_ollama_client", lambda: fake)

    client = TestClient(app)
    client.fake_ollama = fake
    client.test_session = test_session
    yield client
    engine.dispose()


def seed_case(client, case_id="API-TEST-1", verdict="suspicious"):
    """Insert a triage case directly and return it."""
    db = client.test_session()
    case = db_models.TriageResult(
        case_id=case_id,
        rule_name="Test Rule - Certutil Staging",
        rule_id="test_rule",
        verdict=verdict,
        confidence_score=0.5,
        analysis_summary="Synthetic case for API flow tests.",
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    db.close()
    return case_id


def save_evidence(client, case_id, entries, source_system="supportive_manual"):
    resp = client.post(
        f"/api/db/triage/{case_id}/evidence",
        json={"entries": entries, "source_system": source_system, "replace_existing": False},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEvidenceSaveEndpoint:
    def test_failure_status_entries_save_with_empty_result(self, api_client):
        case_id = seed_case(api_client)
        out = save_evidence(api_client, case_id, [
            {"query_title": "No rows query", "result_text": "", "result_status": "no_results"},
            {"query_title": "Broken query", "result_text": "", "result_status": "query_failed"},
        ])
        assert out.get("saved_count") == 2

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id
        ).all()
        db.close()
        assert len(rows) == 2
        statuses = {json.loads(r.raw_result)["result_status"] for r in rows}
        assert statuses == {"no_results", "query_failed"}

    def test_blank_success_entry_is_skipped(self, api_client):
        case_id = seed_case(api_client)
        out = save_evidence(api_client, case_id, [
            {"query_title": "Empty success", "result_text": "", "result_status": "success"},
            {"query_title": "Real one", "result_text": "observed rows", "result_status": "success"},
        ])
        assert out.get("saved_count") == 1


class TestAnalyzeFlow:
    def test_missing_case_returns_404(self, api_client):
        resp = api_client.post("/api/db/analyze", json={"case_id": "DOES-NOT-EXIST"})
        assert resp.status_code == 404

    def test_analyze_persists_per_card_verdicts(self, api_client):
        """The detached-row regression: verdicts the model assessed must land
        in the evidence rows, read back through a fresh session."""
        case_id = seed_case(api_client)
        save_evidence(api_client, case_id, [
            {"query_title": "Certutil network activity",
             "query_text": "index=proxy dest=203.0.113.77",
             "result_text": "12 outbound events observed",
             "result_status": "success"},
            {"query_title": "Change ticket check",
             "result_text": "Only approved changes found",
             "result_status": "success"},
        ])

        resp = api_client.post("/api/db/analyze", json={"case_id": case_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["per_card_verdicts_applied"] == 2

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id
        ).all()
        verdicts = {}
        for r in rows:
            raw = json.loads(r.raw_result)
            verdicts[r.query_title] = (
                raw.get("ai_finding_type"),
                raw.get("ai_verdict_source"),
            )
        db.close()

        # The fake alternates supports/refutes by ledger order (descending
        # created_at: entry 2 first). Both entries must carry per-card data —
        # with the detached-row bug this dict is all (None, None).
        assert set(verdicts) == {"Certutil network activity", "Change ticket check"}
        assert all(source == "per_card" for _, source in verdicts.values())
        assert all(direction in {"supports", "refutes"} for direction, _ in verdicts.values())
        # The two cards must have received OPPOSITE directions (alternating fake)
        directions = {d for d, _ in verdicts.values()}
        assert directions == {"supports", "refutes"}

    def test_analyze_persists_analysis_and_investigation_state(self, api_client):
        case_id = seed_case(api_client)
        # Stage "initial" excludes phase2_manual rows from the prompt but an
        # empty ledger still exercises the analysis + state persistence path.
        resp = api_client.post("/api/db/analyze", json={"case_id": case_id})
        assert resp.status_code == 200

        db = api_client.test_session()
        analysis_rows = db.query(db_models.AnalysisResult).filter(
            db_models.AnalysisResult.case_id == case_id
        ).all()
        state_rows = db.query(db_models.InvestigationState).filter(
            db_models.InvestigationState.case_id == case_id
        ).all()
        db.close()

        assert len(analysis_rows) == 1
        assert analysis_rows[0].model_name  # resolved model echoed
        assert len(state_rows) == 1
        state = json.loads(state_rows[0].evidence_summary)
        assert state["per_card_verdicts_applied"] == 0  # no evidence yet

    def test_analyze_response_reports_resolved_model(self, api_client):
        case_id = seed_case(api_client)
        resp = api_client.post("/api/db/analyze", json={"case_id": case_id, "model": "ghost-tag:missing"})
        assert resp.status_code == 200
        # Fake echoes the requested tag when given one; either way the
        # response must report a concrete non-empty tag, not "".
        assert resp.json()["model"]

    def test_per_card_verdicts_shape_investigation_state(self, api_client):
        """Conflicting per-card verdicts (one supports, one refutes) must put
        the loop into the conflicting-evidence state with a blocker."""
        case_id = seed_case(api_client)
        save_evidence(api_client, case_id, [
            {"query_title": "Card A", "result_text": "Multiple substantive observed rows indicating staging activity", "result_status": "success"},
            {"query_title": "Card B", "result_text": "Multiple substantive observed rows from the change review", "result_status": "success"},
        ])
        resp = api_client.post("/api/db/analyze", json={"case_id": case_id})
        state = resp.json()["investigation_state"]
        assert state["evidence_summary"]["by_finding"]["supports"] >= 1
        assert state["evidence_summary"]["by_finding"]["refutes"] >= 1
        assert any("Conflicting evidence" in b for b in state["closure_blockers"])
        assert state["provisional_disposition"] == "suspicious"


# ---------------------------------------------------------------------------
# Phase 3: one-click search execution (/api/splunk/search-one, mock backend)
# ---------------------------------------------------------------------------

def seed_notable_event(client, case_id, fields):
    """Seed a pasted notable whose fields ground placeholder substitution."""
    db = client.test_session()
    event = db_models.SplunkEvent(
        source="pasted",
        sourcetype="splunk:notable:pasted",
        host=fields.get("host", "TEST-HOST"),
        raw=json.dumps({"promoted_case_id": case_id, "fields": fields}),
    )
    db.add(event)
    db.commit()
    db.close()


def seed_supportive_query(client, rule_id, title, spl):
    db = client.test_session()
    db.add(db_models.SupportiveQuery(rule_id=rule_id, title=title, description="", spl_query=spl))
    db.commit()
    db.close()


class CleanVerdictOllamaClient(FakeOllamaClient):
    """Analysis-only fake for loop-stress runs: a decisive malicious verdict,
    no Key Questions section, no Per-Evidence Assessment — every ledger entry
    falls back to the global 'supports' heuristic. Mirrors a mature
    investigation where the model has stopped asking new questions (the
    reported SPL phase-degradation shape)."""

    def generate(self, prompt, model=None, temperature=None, options=None):
        self.calls.append(prompt)
        return {
            "success": True,
            "model": model or self.model,
            "response": (
                "### Initial Thoughts\nCertutil outbound transfer supports the hypothesis "
                "of unauthorized tool staging.\n\n"
                "### Investigative Analysis\nThe collected rows indicate malicious activity "
                "consistent with compromise.\n\n"
                "### Triage Verdict\nMalicious — true positive.\n"
            ),
        }


def _norm_spl(spl):
    return " ".join((spl or "").split()).lower()


def _post_analyze(client, case_id, phase, stage=None):
    return client.post("/api/db/analyze", json={
        "case_id": case_id,
        "analysis_stage": stage or ("initial" if phase <= 1 else "follow_up"),
        "analysis_phase": phase,
        "prior_analysis": "prior iteration analysis",
    })


def _save_phase_evidence(client, case_id, phase, title, spl=""):
    return client.post(
        f"/api/db/triage/{case_id}/evidence",
        json={
            "source_system": f"phase{phase}_manual",
            "replace_existing": True,
            "entries": [{
                "query_title": title,
                "query_text": spl,
                "result_text": "Multiple substantive observed rows indicating staging activity on the impacted host.",
                "finding_type": "neutral",
                "result_status": "success",
            }],
        },
    )


class TestFollowUpLoopStress:
    """Loop-stress harness — the SPL phase-degradation regression guard
    (DEVELOPMENT_PLAN §8).

    Auto-drives a mock case through N follow-up iterations, one saved
    evidence row per phase, with only 2 playbook templates so the playbook
    is exhausted early. Contract: while the loop still needs work, every
    follow-up analysis must return follow-up cards AND surface at least one
    card carrying a query not yet saved — re-running an identical SPL under
    a new title is not a new query. The loop must never dead-end or replay
    stale SPL, no matter how many iterations run."""

    def test_follow_up_loop_never_dead_ends_or_replays(self, api_client, monkeypatch):
        fake = CleanVerdictOllamaClient()
        monkeypatch.setattr(ollama_service, "get_ollama_client", lambda: fake)
        case_id = seed_case(api_client)
        seed_supportive_query(
            api_client, "test_rule", "Process execution check",
            "sourcetype=linux_secure user=$user$",
        )
        seed_supportive_query(
            api_client, "test_rule", "Outbound destination check",
            "sourcetype=linux_secure host=$host$",
        )

        resp = _post_analyze(api_client, case_id, phase=1)
        assert resp.status_code == 200, resp.text

        saved_spls = set()
        for phase in range(2, 9):  # 7 follow-up iterations vs 2 templates
            resp = _post_analyze(api_client, case_id, phase=phase)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            state = body.get("investigation_state") or {}
            cards = body.get("phase2_queries") or []

            # The harness only means something while the loop still needs work.
            assert state.get("loop_status") != "ready_for_closure", (
                f"phase {phase}: harness assumption broken — loop reached closure early"
            )
            assert cards, (
                f"phase {phase}: loop dead-ended — no follow-up cards while the "
                f"state still needs work (loop_status={state.get('loop_status')})"
            )
            fresh_cards = [
                c for c in cards
                if _norm_spl(c.get("spl")) not in saved_spls
            ]
            assert fresh_cards, (
                f"phase {phase}: no new query surfaced — every card replays an "
                "already-saved SPL (the 'stopped giving new queries' degradation)"
            )

            pick = fresh_cards[0]
            title = (pick.get("title") or "").strip()
            saved_spls.add(_norm_spl(pick.get("spl")))
            ev = _save_phase_evidence(api_client, case_id, phase, title, pick.get("spl") or "")
            assert ev.status_code == 200, ev.text

    def test_follow_up_prompts_keep_all_saved_evidence(self, api_client, monkeypatch):
        """phase3_manual+ evidence must stay visible to later prompts — the
        'just stopped working overall' regression (DEVELOPMENT_PLAN §8)."""
        fake = CleanVerdictOllamaClient()
        monkeypatch.setattr(ollama_service, "get_ollama_client", lambda: fake)
        case_id = seed_case(api_client)

        resp = _post_analyze(api_client, case_id, phase=1)
        assert resp.status_code == 200
        for phase in (2, 3):
            resp = _post_analyze(api_client, case_id, phase=phase)
            assert resp.status_code == 200
            ev = _save_phase_evidence(api_client, case_id, phase, f"Phase {phase} check")
            assert ev.status_code == 200

        resp = _post_analyze(api_client, case_id, phase=4)
        assert resp.status_code == 200
        prompt = fake.calls[-1]
        assert "Phase 2 check" in prompt, "phase2_manual evidence missing from the later prompt"
        assert "Phase 3 check" in prompt, "phase3_manual evidence missing from the later prompt"


class TestSplunkSearchOneEndpoint:
    def _prepare(self, client, fields=None):
        case_id = seed_case(client)
        seed_notable_event(client, case_id, fields or {"host": "VPN-GW-01", "user": "bjones"})
        return case_id

    def test_stored_template_runs_and_ledgers_splunk_auto(self, api_client):
        case_id = self._prepare(api_client)
        seed_supportive_query(
            api_client,
            "test_rule",
            "Failed logins by user",
            "sourcetype=linux_secure action=failure user=$user$",
        )

        resp = api_client.post(
            "/api/splunk/search-one",
            json={"case_id": case_id, "query_title": "Failed logins by user", "earliest": "all"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["result_status"] == "success"
        assert body["row_count"] >= 1
        assert body["source_system"] == "splunk_auto"
        # Server-side substitution: the notable's user value, no raw tokens.
        assert "$user$" not in body["spl"]
        assert "user=bjones" in body["spl"]

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id,
            db_models.SupportiveQueryResult.source_system == "splunk_auto",
        ).all()
        tool_runs = db.query(db_models.ToolRun).filter(
            db_models.ToolRun.tool_name == "splunk_search_one"
        ).all()
        db.close()

        assert len(rows) == 1
        raw = json.loads(rows[0].raw_result)
        assert raw["result_status"] == "success"
        assert raw["query_text"] == body["spl"]
        assert raw["source_system"] == "splunk_auto"
        # Guardrail: the executed query text is in the audit trail.
        assert len(tool_runs) == 1
        assert tool_runs[0].status == "completed"
        assert json.loads(tool_runs[0].arguments)["spl"] == body["spl"]

    def test_zero_rows_maps_to_no_results(self, api_client):
        case_id = self._prepare(api_client, fields={"host": "VPN-GW-01", "user": "nobody"})
        resp = api_client.post(
            "/api/splunk/search-one",
            json={
                "case_id": case_id,
                "query_title": "No-match query",
                "spl": "sourcetype=linux_secure action=failure user=$user$",
                "earliest": "all",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["result_status"] == "no_results"

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id,
            db_models.SupportiveQueryResult.source_system == "splunk_auto",
        ).all()
        db.close()
        assert len(rows) == 1
        assert json.loads(rows[0].raw_result)["result_status"] == "no_results"

    def test_unsupported_spl_maps_to_query_failed(self, api_client):
        case_id = self._prepare(api_client)
        resp = api_client.post(
            "/api/splunk/search-one",
            json={
                "case_id": case_id,
                "query_title": "Broken query",
                "spl": "sourcetype=linux_secure | dedup user",
                "earliest": "all",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["result_status"] == "query_failed"
        assert "unsupported SPL operator" in body["error"]

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id,
            db_models.SupportiveQueryResult.source_system == "splunk_auto",
        ).all()
        tool_runs = db.query(db_models.ToolRun).filter(
            db_models.ToolRun.tool_name == "splunk_search_one"
        ).all()
        db.close()
        assert json.loads(rows[0].raw_result)["result_status"] == "query_failed"
        assert tool_runs[0].status == "failed"

    def test_unknown_title_without_spl_returns_404(self, api_client):
        case_id = self._prepare(api_client)
        resp = api_client.post(
            "/api/splunk/search-one",
            json={"case_id": case_id, "query_title": "Missing query"},
        )
        assert resp.status_code == 404

    def test_unresolved_placeholder_returns_422(self, api_client):
        case_id = self._prepare(api_client)
        resp = api_client.post(
            "/api/splunk/search-one",
            json={
                "case_id": case_id,
                "query_title": "Half-rendered",
                "spl": "sourcetype=linux_secure host=$nope_token$",
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "nope_token" in detail["unresolved_tokens"]

    def test_rerun_replaces_prior_auto_row(self, api_client):
        case_id = self._prepare(api_client)
        payload = {
            "case_id": case_id,
            "query_title": "Failed logins",
            "spl": "sourcetype=linux_secure action=failure user=$user$",
            "earliest": "all",
        }
        first = api_client.post("/api/splunk/search-one", json=payload)
        second = api_client.post("/api/splunk/search-one", json=payload)
        assert first.status_code == second.status_code == 200

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id,
            db_models.SupportiveQueryResult.source_system == "splunk_auto",
        ).all()
        db.close()
        assert len(rows) == 1

    def test_per_case_concurrency_cap_rejects_second_run(self, api_client):
        case_id = self._prepare(api_client)
        api_main._SEARCH_ONE_INFLIGHT.add(case_id)
        try:
            resp = api_client.post(
                "/api/splunk/search-one",
                json={
                    "case_id": case_id,
                    "query_title": "Q",
                    "spl": "sourcetype=linux_secure action=failure user=$user$",
                },
            )
            assert resp.status_code == 409
        finally:
            api_main._SEARCH_ONE_INFLIGHT.discard(case_id)

    def test_timeout_maps_to_query_failed(self, api_client, monkeypatch):
        case_id = self._prepare(api_client)
        monkeypatch.setattr(api_main, "_SEARCH_ONE_TIMEOUT_SECONDS", 0.05)

        def slow_search(self, spl, earliest="-7d", latest="now", limit=500):
            time.sleep(0.5)
            return []

        monkeypatch.setattr(search_backend_mod.MockSplunkBackend, "search", slow_search)
        resp = api_client.post(
            "/api/splunk/search-one",
            json={
                "case_id": case_id,
                "query_title": "Slow query",
                "spl": "sourcetype=linux_secure action=failure user=$user$",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["result_status"] == "query_failed"
        assert "timed out" in body["error"]

        db = api_client.test_session()
        rows = db.query(db_models.SupportiveQueryResult).filter(
            db_models.SupportiveQueryResult.case_id == case_id,
            db_models.SupportiveQueryResult.source_system == "splunk_auto",
        ).all()
        db.close()
        assert len(rows) == 1
        assert json.loads(rows[0].raw_result)["result_status"] == "query_failed"


# ---------------------------------------------------------------------------
# Phase 4: closure-note judgment-flow lockdown
# ---------------------------------------------------------------------------

def seed_investigation_state(client, case_id, provisional_disposition):
    """Insert a decisive investigation state directly (deterministic tests)."""
    db = client.test_session()
    db.add(db_models.InvestigationState(
        case_id=case_id,
        rule_id="test_rule",
        current_hypothesis="Test hypothesis grounded in collected rows.",
        provisional_disposition=provisional_disposition,
        disposition_confidence=0.9,
        loop_status="ready_for_closure",
        iteration_count=2,
        unresolved_questions="[]",
        closure_blockers="[]",
        recommended_next_actions="[]",
        evidence_summary=json.dumps({"substantive_items": 2, "by_finding": {"supports": 2}}),
        last_analysis_stage="initial",
    ))
    db.commit()
    db.close()


class TestClosureNoteDispositionDerivation:
    """The operator must not be able to pre-set the closure disposition; it
    is derived from the evidence-backed investigation state (plan §6)."""

    def test_operator_disposition_cannot_override_derived(self, api_client):
        case_id = seed_case(api_client)
        seed_investigation_state(api_client, case_id, "malicious")
        resp = api_client.post("/api/db/closure-note", json={
            "case_id": case_id,
            "rule_id": "test_rule",
            "disposition": "False Positive",
            "field_values": {"justification": "Test/training exercise", "host": "WIN-APP-042"},
            "analyst_notes": "Observed during the approved change window.",
            "force_closure": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["disposition"].lower().startswith("true positive")
        assert body["disposition_source"] == "investigation_state"
        assert body["operator_disposition"] == "False Positive"
        assert body["disposition_conflict"] is True

        note = body["generated_note"].lower()
        assert "the evidence-driven conclusion was true positive" in note
        assert "evidence-driven conclusion was false positive" not in note
        # field_values render only as attributed operator facts.
        assert "operator-recorded closure fields" in note
        assert "test/training exercise" in note

    def test_matching_operator_disposition_reports_no_conflict(self, api_client):
        case_id = seed_case(api_client)
        seed_investigation_state(api_client, case_id, "benign")
        resp = api_client.post("/api/db/closure-note", json={
            "case_id": case_id,
            "rule_id": "test_rule",
            "disposition": "Benign Positive",
            "force_closure": True,
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["disposition"].lower().startswith("benign positive")
        assert body["disposition_conflict"] is False
        assert body["disposition_key"] == "benign_positive"

    def test_field_values_cannot_smuggle_a_conclusion(self, api_client):
        """A disposition-shaped field value must never reach the note's
        conclusion sentence — only the attributed operator section."""
        case_id = seed_case(api_client)
        seed_investigation_state(api_client, case_id, "benign")
        resp = api_client.post("/api/db/closure-note", json={
            "case_id": case_id,
            "rule_id": "test_rule",
            "disposition": "True Positive",
            "field_values": {"justification": "Known false positive pattern"},
            "force_closure": True,
        })
        assert resp.status_code == 200, resp.text
        note = resp.json()["generated_note"].lower()
        conclusion_lines = [ln for ln in note.splitlines() if "evidence-driven conclusion" in ln]
        assert len(conclusion_lines) == 1
        assert "benign positive" in conclusion_lines[0]
        assert "false positive" not in conclusion_lines[0]
        # The value is still preserved, attributed, elsewhere in the note.
        assert "known false positive pattern" in note


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
