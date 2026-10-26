"""The narrative demo corpus: shape, coherence, and the flow it feeds.

The generator is exercised through its CLI (the way a human runs it), then the
corpus is queried through the search backend and — for the incident's own
cards — through the platform's paste -> promote -> search-one path against an
isolated SQLite database and the fake Splunk server.

What this pins down:
  - ~500 events land across real indexes, anchored to now (so the platform's
    default -7d / -2h windows are not empty — the trap that made the committed
    fixture useless a month after it was written);
  - every playbook card executes inside MockSplunkBackend's SPL subset, and
    five of the six return evidence while the change-record card is empty;
  - the notable pastes, promotes, and its fields drive placeholder
    substitution all the way to a real HTTP search.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

INDEXES = {"wineventlog", "edr", "proxy", "netflow", "auth", "itsm", "notable"}
# Hyphen-free on purpose: the paste pipeline's default redaction tokenizes
# `word-word`, which would rewrite the notable's host before $host$ is resolved.
CASE_HOST = "FINWS07"
CASE_USER = "mrivera"


def _load_script(name, filename):
    path = ROOT / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def corpus(tmp_path):
    """Generate the corpus with the real CLI, anchored to this test's now."""
    module = _load_script("soc_demo_corpus", "seed_demo_corpus.py")
    anchor = datetime.now().replace(microsecond=0)
    exit_code = module.main(["--out-dir", str(tmp_path), "--anchor", anchor.isoformat()])
    assert exit_code == 0
    events = [
        json.loads(line)
        for line in (tmp_path / "demo_events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return module, tmp_path, events, anchor


@pytest.fixture()
def demo_backend(corpus):
    from services.search_backend import MockSplunkBackend

    _module, out_dir, _events, _anchor = corpus
    return MockSplunkBackend(seed_path=out_dir / "demo_events.jsonl")


def test_corpus_is_500_events_across_real_indexes(corpus):
    module, _out_dir, events, _anchor = corpus

    assert len(events) == module.TARGET_EVENTS == 500
    assert {event["index"] for event in events} == INDEXES
    # Every event must be filterable: metadata plus at least one k=v field.
    for event in events:
        assert event["index"] and event["sourcetype"] and event["host"]
        assert "=" in event["raw"]
        assert event["timestamp"]


def test_corpus_is_anchored_to_now(corpus):
    """The staleness trap: a corpus generated weeks ago answers nothing."""
    _module, _out_dir, events, anchor = corpus
    stamps = [datetime.fromisoformat(event["timestamp"]) for event in events]

    assert max(stamps) <= anchor + timedelta(minutes=1)
    assert anchor - max(stamps) <= timedelta(minutes=30)   # incident is recent
    assert anchor - min(stamps) <= timedelta(days=8)        # baseline fits -7d


def test_incident_is_visible_in_the_default_window(corpus, demo_backend):
    backend = demo_backend

    powershell = backend.search(
        f"index=edr sourcetype=edr:process host={CASE_HOST} process=powershell.exe",
        earliest="-7d",
    )
    assert len(powershell) == 2
    assert all(row["index"] == "edr" for row in powershell)

    beacons = backend.search("index=proxy dest=cdn-sync.example.net", earliest="-7d")
    assert len(beacons) == 25  # 24 beacon GETs + the exfil POST

    notable = backend.search("index=notable", earliest="-7d")
    assert len(notable) == 1
    assert "DEMO_RULE_PS_EXFIL" in notable[0]["raw"]

    geos = {
        row["geo"]
        for row in backend.search(
            f"index=auth user={CASE_USER} action=success | stats count by geo",
            earliest="-7d",
        )
    }
    assert geos == {"Ashburn", "Reston", "Bucharest"}


def test_playbook_cards_fit_the_subset_and_find_the_evidence(corpus, demo_backend):
    module, out_dir, _events, _anchor = corpus
    from api.routes.notables import parse_structured_notable
    from services.rule_context_service import render_query_template

    fields = parse_structured_notable((out_dir / "demo_notable.txt").read_text(encoding="utf-8"))
    results = {}
    for query in module.PLAYBOOK_QUERIES:
        spl, unresolved = render_query_template(query["spl"], fields)
        assert unresolved == [], f"{query['title']} has unresolved placeholders"
        assert "$" not in spl
        # Raises ValueError here if the SPL leaves the supported subset.
        results[query["title"]] = demo_backend.search(spl, earliest="-7d")

    evidence_cards = [query["title"] for query in module.PLAYBOOK_QUERIES[:5]]
    for title in evidence_cards:
        assert results[title], f"{title} returned no evidence"
    # The refuting card must be genuinely empty — no approved change exists for
    # this host, which is itself the finding.
    assert results["Approved change records for case host"] == []


def test_notable_renders_placeholder_source_fields(corpus):
    module, out_dir, _events, _anchor = corpus
    from api.routes.notables import parse_structured_notable

    fields = parse_structured_notable((out_dir / "demo_notable.txt").read_text(encoding="utf-8"))
    assert fields["host"] == CASE_HOST
    assert fields["user"] == CASE_USER
    assert fields["correlation_search"] == module.DEMO_RULE_NAME
    assert fields["rule_id"] == module.DEMO_RULE_ID
    assert fields["time"].startswith(str(datetime.now().year))


def test_demo_flow_pastes_promotes_and_runs_cards(db_client, corpus, tmp_path, monkeypatch):
    """The whole demo, end to end: paste -> promote -> real HTTP search per card."""
    module, out_dir, _events, _anchor = corpus
    fake_module = _load_script("soc_fake_splunk_demo", "fake_splunk.py")

    # The paste path stages files; keep them in the test's tmp dir.
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(tmp_path / "quarantine"))

    from api import auth

    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")

    with fake_module.FakeSplunkServer(
        token="demo-token", seed_path=out_dir / "demo_events.jsonl"
    ) as server:
        monkeypatch.setenv("SPLUNK_URL", server.base_url)
        monkeypatch.setenv("SPLUNK_TOKEN", "demo-token")

        # Playbooks go in exactly the way the --db flag writes them.
        module.seed_db(module.PLAYBOOK_QUERIES)

        notable_text = (out_dir / "demo_notable.txt").read_text(encoding="utf-8")
        paste = db_client.post("/api/db/notables/paste", json={"raw_text": notable_text})
        assert paste.status_code == 200, paste.text
        event_id = paste.json()["event_id"]
        assert event_id

        promote = db_client.post(f"/api/db/notables/{event_id}/promote")
        assert promote.status_code == 200, promote.text
        case_id = promote.json()["case_id"]
        assert promote.json()["verdict"] == "suspicious"  # intake never pre-judges

        # Default redaction is ON in the UI; the demo identifiers must survive
        # it, or every card would query a HOST_N token instead of the host.
        from services.evidence_service import resolve_source_notable_for_case

        session = db_client.test_session()
        stored_fields = resolve_source_notable_for_case(session, case_id)["fields"]
        session.close()
        assert stored_fields["host"] == CASE_HOST
        assert stored_fields["user"] == CASE_USER

        expectations = {
            "Encoded PowerShell lineage on case host": "success",
            "VPN logon history for case user": "success",
            "Lateral logons from case host": "success",
            "Approved change records for case host": "no_results",
        }
        for title, expected in expectations.items():
            resp = db_client.post(
                "/api/splunk/search-one",
                json={"case_id": case_id, "query_title": title, "earliest": "-7d"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["backend"] == "RealSplunkBackend"
            assert body["result_status"] == expected, (title, body.get("error"))
            # Placeholders came from the pasted notable, not from a literal.
            assert CASE_HOST in body["spl"] or CASE_USER in body["spl"]
            assert "$" not in body["spl"]

    import db.models as db_models

    db = db_client.test_session()
    ledger = db.query(db_models.SupportiveQueryResult).filter(
        db_models.SupportiveQueryResult.case_id == case_id,
    ).all()
    db.close()
    statuses = sorted(json.loads(row.raw_result)["result_status"] for row in ledger)
    assert statuses == ["no_results", "success", "success", "success"]
