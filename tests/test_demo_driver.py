"""The demo driver must read the fields the routes actually return.

`scripts/e2e_demo_loop_test.py` is run by hand before a demo, so a wrong key
reads exactly like "the loop never reached closure". It used to check
`state["ready_for_closure"]`, `readiness["ready_for_closure"]`/`["eligible"]`
and `state["active_blockers"]` — none of which the API ever returns — so a
healthy run reported three failures.

These tests pin the closure signals to the real payload shapes:
investigation state carries `loop_status` + `closure_blockers`; the readiness
endpoint carries `is_ready`, `warnings`, `blockers`.

`test_healthy_loop_passes_against_the_live_routes` is the important one: it
drives the real analyze + closure-readiness routes with a fake model and
asserts the driver's own checks pass on the returned payloads. The dict-level
tests above only prove the parser; that one proves the field names.
"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location(
        "soc_e2e_demo_loop", ROOT / "scripts" / "e2e_demo_loop_test.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _checks_by_name(result):
    return {name: (ok, detail) for name, ok, detail in result}


def test_ready_loop_passes_without_the_old_dead_keys():
    """A genuinely ready loop, in the shape the API actually returns."""
    driver = _driver()
    # No `ready_for_closure` boolean, no `eligible`, no `active_blockers` —
    # exactly what analyze + closure-readiness emit.
    state = {
        "loop_status": "ready_for_closure",
        "closure_blockers": [],
        "unresolved_questions": [],
        "disposition_confidence": 0.86,
    }
    readiness = {
        "is_ready": True,
        "status": "ready_for_closure",
        "confidence": 0.86,
        "provisional_disposition": "malicious",
        "blockers": [],
        "warnings": [],
    }

    checks = _checks_by_name(driver.closure_checks(state, readiness))
    assert set(checks) == {
        "ready_for_closure_flag",
        "closure_gate_reachable",
        "closure_warnings_clear",
    }
    assert all(ok for ok, _detail in checks.values()), checks


def test_not_ready_loop_reports_every_reason():
    driver = _driver()
    state = {
        "loop_status": "collecting_evidence",
        "closure_blockers": ["Conflicting evidence on host FINWS07."],
        "disposition_confidence": 0.62,
    }
    readiness = {
        "is_ready": False,
        "confidence": 0.62,
        "blockers": ["Conflicting evidence on host FINWS07."],
        "warnings": [
            "Disposition confidence (62%) is below the 80% closure threshold.",
            "1 investigative question(s) remain open.",
        ],
    }

    checks = _checks_by_name(driver.closure_checks(state, readiness))
    assert not any(ok for ok, _detail in checks.values())
    assert "collecting_evidence" in checks["ready_for_closure_flag"][1]
    assert "blockers=1" in checks["ready_for_closure_flag"][1]
    assert "below the 80%" in checks["closure_warnings_clear"][1]


def test_blockers_come_from_the_investigation_state():
    """Analyze's state is authoritative even before readiness is fetched."""
    driver = _driver()
    state = {
        "loop_status": "ready_for_closure",
        "closure_blockers": ["1 investigative question(s) remain open."],
    }

    checks = _checks_by_name(driver.closure_checks(state, {}))
    assert checks["ready_for_closure_flag"][0] is True
    assert "blockers=1" in checks["ready_for_closure_flag"][1]
    # Without a readiness payload the gate cannot be claimed reachable.
    assert checks["closure_gate_reachable"][0] is False


def test_missing_payloads_fail_closed():
    driver = _driver()
    checks = _checks_by_name(driver.closure_checks(None, None))
    assert all(ok is False for ok, _detail in checks.values())


class _CleanVerdictClient:
    """Fake model: decisive verdict, no open questions, nothing to block on."""

    available = True
    model = "fake-model:latest"

    def generate(self, prompt, model=None, temperature=None, options=None):
        return {
            "success": True,
            "model": model or self.model,
            "response": (
                "### Investigative Analysis\nActivity is consistent with compromise.\n\n"
                "### Triage Verdict\nMalicious. Encoded payload executed and called out.\n"
            ),
        }


def test_healthy_loop_passes_against_the_live_routes(db_client, monkeypatch):
    """End to end: a case that genuinely reached closure must pass every check.

    Seeds the case the way promotion does — `suspicious` at exactly the 0.80
    closure-gate baseline — saves substantive evidence, then asks the real
    routes. The driver's checks are run on the actual response payloads, so a
    renamed field anywhere in analyze or closure-readiness fails here instead
    of at the podium.
    """
    import api.routes.promote as promote_routes
    import db.models as db_models
    import services.ollama_service as ollama_service

    monkeypatch.setattr(
        ollama_service, "get_ollama_client", lambda: _CleanVerdictClient()
    )

    case_id = "DEMO-DRIVER-1"
    db = db_client.test_session()
    db.add(
        db_models.TriageResult(
            case_id=case_id,
            rule_name="Demo Rule",
            rule_id="demo_rule",
            verdict="suspicious",
            confidence_score=promote_routes.derive_triage_confidence(""),
            analysis_summary="Synthetic case for the demo driver contract.",
        )
    )
    db.add(
        db_models.SplunkEvent(
            source="pasted",
            sourcetype="splunk:notable:pasted",
            host="FINWS07",
            raw=json.dumps({"promoted_case_id": case_id, "fields": {"host": "FINWS07"}}),
        )
    )
    db.commit()
    db.close()

    entries = [
        {
            "query_title": f"Grounded check {i}",
            "query_text": "index=edr host=FINWS07",
            "result_text": (
                "Correlated events show the encoded payload running and calling "
                f"out to an external host (sample {i}); no maintenance ticket."
            ),
            "result_status": "success",
        }
        for i in range(3)
    ]
    saved = db_client.post(
        f"/api/db/triage/{case_id}/evidence",
        json={"entries": entries, "source_system": "phase2_manual"},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json().get("saved_count") == 3

    analyzed = db_client.post(
        "/api/db/analyze",
        json={
            "case_id": case_id,
            "analysis_stage": "follow_up",
            "prior_analysis": "prior round",
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    state = analyzed.json().get("investigation_state") or {}

    readiness = db_client.get(f"/api/db/triage/{case_id}/closure-readiness")
    assert readiness.status_code == 200, readiness.text

    checks = _checks_by_name(_driver().closure_checks(state, readiness.json()))
    assert all(ok for ok, _detail in checks.values()), checks
    # And the state really is the ready shape, not an empty payload that
    # happened to satisfy the checks.
    assert state["loop_status"] == "ready_for_closure"
    assert readiness.json()["is_ready"] is True
    assert float(state["disposition_confidence"]) >= 0.80
