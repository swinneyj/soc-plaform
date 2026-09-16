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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
