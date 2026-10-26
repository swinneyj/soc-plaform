"""Tests for the pluggable search backend (Phase 3).

The mock backend must be deterministic and hermetic: most tests build a tiny
inline seed file in tmp_path so they never depend on clock or seed-file drift.
"""

import json
from datetime import datetime, timedelta

import pytest

from services.search_backend import (
    MockSplunkBackend,
    RealSplunkBackend,
    SplunkSearchError,
    get_search_backend,
    map_search_outcome,
    summarize_search_rows,
)
from services.rule_context_service import render_query_template


def _write_seed(tmp_path, events):
    seed = tmp_path / "seed_events.jsonl"
    with open(seed, "w", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    return seed


@pytest.fixture()
def seed_events():
    now = datetime.now()
    return [
        {
            "source": "/var/log/secure",
            "sourcetype": "linux_secure",
            "host": "VPN-GW-01",
            "timestamp": (now - timedelta(hours=1)).isoformat(),
            "raw": "sshd: action=failure user=bjones src_ip=10.0.0.1 geo=Ashburn app=ssh",
        },
        {
            "source": "/var/log/secure",
            "sourcetype": "linux_secure",
            "host": "VPN-GW-01",
            "timestamp": (now - timedelta(hours=2)).isoformat(),
            "raw": "sshd: action=failure user=asmith src_ip=10.0.0.2 geo=Ashburn app=ssh",
        },
        {
            "source": "/var/log/secure",
            "sourcetype": "linux_secure",
            "host": "VPN-GW-02",
            "timestamp": (now - timedelta(days=3)).isoformat(),
            "raw": "sshd: action=success user=bjones src_ip=10.0.0.3 geo=Berlin app=ssh",
        },
        {
            # deliberately outside the default -7d window
            "source": "/var/log/secure",
            "sourcetype": "linux_secure",
            "host": "VPN-GW-01",
            "timestamp": (now - timedelta(days=30)).isoformat(),
            "raw": "sshd: action=failure user=bjones src_ip=10.0.0.9 geo=Ashburn app=ssh",
        },
    ]


@pytest.fixture()
def backend(tmp_path, seed_events):
    return MockSplunkBackend(seed_path=_write_seed(tmp_path, seed_events))


# ---------------------------------------------------------------------------
# Filtering / parsing
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Index metadata + wildcard field terms
# ---------------------------------------------------------------------------

def _indexed_seed(tmp_path):
    now = datetime.now()
    return _write_seed(tmp_path, [
        {"index": "auth", "source": "/var/log/secure", "sourcetype": "vpn:auth",
         "host": "VPN-GW-01", "timestamp": (now - timedelta(minutes=10)).isoformat(),
         "raw": "action=success user=bjones geo=Ashburn"},
        {"index": "edr", "source": "EDR", "sourcetype": "edr:process",
         "host": "FINWS07", "timestamp": (now - timedelta(minutes=8)).isoformat(),
         "raw": "process=powershell.exe user=bjones"},
        {"index": "notable", "source": "ES", "sourcetype": "notable",
         "host": "FINWS07", "timestamp": (now - timedelta(minutes=5)).isoformat(),
         "raw": "search_name=Demo rule_id=DEMO"},
    ])


def test_index_term_filters_on_event_metadata(tmp_path):
    backend = MockSplunkBackend(seed_path=_indexed_seed(tmp_path))

    assert len(backend.search("index=edr", earliest="all")) == 1
    assert len(backend.search("index=*", earliest="all")) == 3
    assert backend.search("index=proxy", earliest="all") == []
    # Events without an explicit index default to `main`, like Splunk.
    assert backend.search("index=main", earliest="all") == []

    by_index = backend.search("index=* | stats count by index", earliest="all")
    assert {row["index"]: row["count"] for row in by_index} == {
        "auth": 1, "edr": 1, "notable": 1,
    }


def test_wildcard_field_values_match(tmp_path):
    backend = MockSplunkBackend(seed_path=_indexed_seed(tmp_path))

    assert len(backend.search("index=e*", earliest="all")) == 1        # edr
    assert len(backend.search("host=FIN*", earliest="all")) == 2
    assert len(backend.search("host=FINWS0?", earliest="all")) == 2
    assert len(backend.search("sourcetype=edr:*", earliest="all")) == 1
    assert len(backend.search("host=VPN-*", earliest="all")) == 1
    # A wildcard still matches nothing when the field is absent.
    assert backend.search("index=* category=*", earliest="all") == []


def test_newest_event_at_reports_corpus_anchor(tmp_path):
    backend = MockSplunkBackend(seed_path=_indexed_seed(tmp_path))
    newest = backend.newest_event_at()
    assert newest is not None
    assert abs((datetime.now() - newest).total_seconds()) < 60 * 11


def test_base_kv_filtering(backend):
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="all")
    assert len(rows) == 3  # includes the 30-day-old event


def test_default_time_window_excludes_old_events(backend):
    rows = backend.search("sourcetype=linux_secure user=bjones")
    assert len(rows) == 2  # -7d window drops the 30-day-old event


def test_quoted_values_and_multiple_terms(backend):
    rows = backend.search('sourcetype=linux_secure action=failure user="bjones"', earliest="-7d")
    assert len(rows) == 1
    assert "action=failure" in rows[0]["raw"]


def test_free_text_term_in_base_search(backend):
    rows = backend.search("sourcetype=linux_secure Berlin", earliest="all")
    assert len(rows) == 1
    assert "Berlin" in rows[0]["raw"]


def test_pipe_search_narrows(backend):
    rows = backend.search(
        "sourcetype=linux_secure | search action=success", earliest="all"
    )
    assert len(rows) == 1
    assert rows[0]["host"] == "VPN-GW-02"


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


def test_explicit_iso_earliest(backend):
    cutoff = datetime.now() - timedelta(days=10)
    rows = backend.search("sourcetype=linux_secure", earliest=cutoff.isoformat())
    assert len(rows) == 3  # everything except the 30-day-old event


def test_earliest_older_window_includes_old_event(backend):
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="-60d")
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# Stats / head / errors
# ---------------------------------------------------------------------------


def test_stats_count_by_field(backend):
    rows = backend.search("sourcetype=linux_secure | stats count by user", earliest="all")
    # The raw kv fields live inside the event body; the stats field must
    # resolve against the merged field map (event fields + metadata).
    assert rows, "stats returned no rows"
    counts = {r["user"]: r["count"] for r in rows}
    assert counts["bjones"] == 3
    assert counts["asmith"] == 1


def test_stats_counts_are_sorted_descending(backend):
    rows = backend.search("sourcetype=linux_secure | stats count by user", earliest="all")
    counts_only = [r["count"] for r in rows]
    assert counts_only == sorted(counts_only, reverse=True)


def test_head_limits_rows(backend):
    rows = backend.search("sourcetype=linux_secure | head 2", earliest="all")
    assert len(rows) == 2


def test_rows_are_newest_first(backend):
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="all")
    stamps = [r["timestamp"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)


def test_row_shape_matches_splunk_event_columns(backend):
    """SplunkEvent columns plus `index` — the metadata Splunk itself returns.

    Nothing constructs an ORM row from these dicts (verified: every
    SplunkEvent(...) call site passes explicit columns), so carrying index is
    additive: it lets the evidence payload show which index a row came from.
    """
    row = backend.search("sourcetype=linux_secure user=asmith", earliest="all")[0]
    assert set(row.keys()) == {
        "index", "source", "sourcetype", "host", "timestamp", "raw",
    }
    assert row["index"] == "main"  # seeded rows without metadata default here


def test_unsupported_operator_raises(backend):
    with pytest.raises(ValueError, match="unsupported SPL operator"):
        backend.search("sourcetype=linux_secure | dedup user", earliest="all")


# ---------------------------------------------------------------------------
# Case-result integration shape
# ---------------------------------------------------------------------------


def test_execute_for_case_shape(backend):
    result = backend.execute_for_case(
        case_id="MOCK-CASE-001",
        rule_id="MOCK-RULE-001",
        query_title="Failed logins for user",
        spl="sourcetype=linux_secure action=failure user=bjones",
        earliest="-7d",
    )
    assert result["case_id"] == "MOCK-CASE-001"
    assert result["source_system"] == "splunk"
    payload = json.loads(result["raw_result"])
    assert payload["backend"] == "mock"
    assert payload["row_count"] == len(payload["rows"]) == 1
    assert payload["rows"][0]["raw"].startswith("sshd:")


# ---------------------------------------------------------------------------
# Factory / stub behavior
# ---------------------------------------------------------------------------


def test_factory_default_is_mock(monkeypatch):
    monkeypatch.delenv("SEARCH_BACKEND", raising=False)
    assert isinstance(get_search_backend(), MockSplunkBackend)


def test_factory_selects_mock_explicitly(monkeypatch):
    assert isinstance(get_search_backend("mock"), MockSplunkBackend)


def test_factory_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unknown SEARCH_BACKEND"):
        get_search_backend("elastic")


def test_real_backend_fails_loudly_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SPLUNK_URL", raising=False)
    monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
    with pytest.raises(SplunkSearchError, match="SPLUNK_URL"):
        RealSplunkBackend()


# ---------------------------------------------------------------------------
# Phase 3 pure helpers: job-outcome mapping and placeholder substitution
# ---------------------------------------------------------------------------

def test_map_search_outcome_rows_found_is_success():
    assert map_search_outcome(3) == "success"


def test_map_search_outcome_zero_rows_is_no_results():
    assert map_search_outcome(0) == "no_results"
    assert map_search_outcome(None) == "no_results"


def test_map_search_outcome_error_wins_over_row_count():
    assert map_search_outcome(5, error="boom") == "query_failed"
    assert map_search_outcome(0, error="timed out") == "query_failed"


def test_summarize_search_rows_error_and_empty():
    assert summarize_search_rows([], error="boom") == "Search failed: boom"
    assert summarize_search_rows([]).startswith("0 rows returned")


def test_summarize_search_rows_lists_rows():
    rows = [{"raw": "sshd: action=failure user=bjones"}, {"host": "H1", "count": 2}]
    text = summarize_search_rows(rows)
    assert "2 row(s) returned" in text
    assert "sshd: action=failure user=bjones" in text
    assert "host=H1 count=2" in text


def test_summarize_search_rows_caps_and_truncates():
    rows = [{"raw": f"row-{i}"} for i in range(30)]
    text = summarize_search_rows(rows, max_rows=5)
    assert "showing first 5" in text
    assert "25 more row(s) omitted" in text
    assert "row-29" not in text
    tiny = summarize_search_rows(rows, max_rows=5, max_chars=60)
    assert "summary truncated" in tiny


def test_render_query_template_direct_field_match():
    rendered, unresolved = render_query_template("user=$user$ action=failure", {"user": "bjones"})
    assert rendered == "user=bjones action=failure"
    assert unresolved == []


def test_render_query_template_default_alias():
    rendered, unresolved = render_query_template("dest=$dest$", {"destination": "srv-01"})
    assert rendered == "dest=srv-01"
    assert unresolved == []


def test_render_query_template_custom_alias():
    custom = [{"alias": "host", "fields": ["computer_name"]}]
    rendered, unresolved = render_query_template("host=$host$", {"computer_name": "WIN-01"}, custom)
    assert rendered == "host=WIN-01"
    assert unresolved == []


def test_render_query_template_reports_unresolved_tokens():
    rendered, unresolved = render_query_template("host=$host$ user={who}", {})
    assert "$host$" in rendered and "{who}" in rendered
    assert unresolved == ["host", "who"]


# ---------------------------------------------------------------------------
# Embedded search-time qualifiers (earliest=/latest= inside the SPL text)
# ---------------------------------------------------------------------------


def test_embedded_earliest_overrides_caller_window(backend):
    # Re-check variants embed a phase-scoped window in the SPL text itself
    # (api.helpers.phase2._rescope_variant_spl); it must win over the job-level window.
    assert len(backend.search("sourcetype=linux_secure user=bjones", earliest="all")) == 3
    rows = backend.search("sourcetype=linux_secure user=bjones earliest=-2d", earliest="all")
    assert len(rows) == 1  # the 3-day-old and 30-day-old events drop out


def test_embedded_earliest_is_not_treated_as_a_field_match(backend):
    # Regression: parsing earliest=-2h as an earliest=="-2h" kv-term matched
    # nothing, so every re-check variant silently returned 0 rows.
    rows = backend.search("sourcetype=linux_secure user=bjones earliest=-2h", earliest="all")
    assert len(rows) == 1
    assert "src_ip=10.0.0.1" in rows[0]["raw"]


def test_embedded_latest_narrows_upper_bound(backend):
    rows = backend.search("sourcetype=linux_secure earliest=all latest=-2d")
    assert len(rows) == 2  # only the 3-day-old and 30-day-old events


def test_embedded_qualifiers_survive_pipe_ops(backend):
    rows = backend.search(
        "sourcetype=linux_secure user=bjones earliest=-2d | stats count by host",
        earliest="all",
    )
    assert len(rows) == 1
    assert rows[0]["count"] == 1


# ---------------------------------------------------------------------------
# RealSplunkBackend: read-only REST connector contract
# ---------------------------------------------------------------------------

class FakeSplunkResponse:
    def __init__(self, text="", status_code=200, json_data=None):
        self.text = text
        self.status_code = status_code
        self._json_data = json_data

    def json(self):
        if self._json_data is None:
            raise ValueError("response body is not JSON")
        return self._json_data


class FakeSplunkSession:
    """Duck-typed requests.Session: records the request, returns a canned reply."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def post(self, url, headers=None, data=None, timeout=None, verify=None):
        self.calls.append(
            {"url": url, "headers": headers, "data": data, "timeout": timeout, "verify": verify}
        )
        if self._error is not None:
            raise self._error
        return self._response

    def get(self, url, headers=None, params=None, timeout=None, verify=None):
        self.calls.append(
            {"url": url, "headers": headers, "params": params, "timeout": timeout,
             "verify": verify}
        )
        if self._error is not None:
            raise self._error
        return self._response


def _real_backend(session, **kwargs):
    kwargs.setdefault("base_url", "https://splunk.example.com:8089")
    kwargs.setdefault("token", "test-token")
    kwargs.setdefault("timeout", 42.0)
    return RealSplunkBackend(session=session, **kwargs)


def test_real_backend_export_request_shape():
    session = FakeSplunkSession(FakeSplunkResponse("_time,raw\n"))
    backend = _real_backend(session)
    backend.search("sourcetype=linux_secure user=bjones", earliest="-15m", limit=25)

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://splunk.example.com:8089/services/search/jobs/export"
    assert call["headers"] == {"Authorization": "Bearer test-token"}
    assert call["timeout"] == 42.0
    # Verification is on by default — never silently off.
    assert call["verify"] is True
    assert call["data"] == {
        "search": "search sourcetype=linux_secure user=bjones",
        "earliest_time": "-15m",
        "latest_time": "now",
        "max_count": 25,
        "output_mode": "csv",
    }


def test_real_backend_prepends_search_command():
    assert RealSplunkBackend._export_search_expr("user=bjones") == "search user=bjones"


def test_real_backend_generating_commands_get_no_prefix():
    for spl in (
        "| stats count by user",
        "savedsearch my_saved_search",
        "loadjob savedsearch_id",
        "rest /services/server/info",
        "makeresults count=5",
        "SEARCH user=bjones",  # case-insensitive
    ):
        assert RealSplunkBackend._export_search_expr(spl) == spl


def test_real_backend_normalizes_csv_rows():
    csv_text = (
        "_time,source,sourcetype,host,raw\n"
        '1758824043,/var/log/secure,linux_secure,VPN-GW-01,"sshd: action=failure user=bjones"\n'
    )
    backend = _real_backend(FakeSplunkSession(FakeSplunkResponse(csv_text)))
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="-15m")

    assert len(rows) == 1
    row = rows[0]
    # epoch 1758824043 -> naive-UTC ISO (platform timestamp convention)
    assert row["timestamp"].startswith("2025-09-")
    assert row["source"] == "/var/log/secure"
    assert row["sourcetype"] == "linux_secure"
    assert row["host"] == "VPN-GW-01"
    assert row["raw"] == "sshd: action=failure user=bjones"


def test_real_backend_stats_rows_get_shape_defaults():
    csv_text = "user,count\nbjones,3\nasmith,1\n"
    backend = _real_backend(FakeSplunkSession(FakeSplunkResponse(csv_text)))
    rows = backend.search("sourcetype=linux_secure | stats count by user", earliest="all")

    assert rows[0] == {
        "user": "bjones",
        "count": "3",
        "source": "",
        "sourcetype": "",
        "host": "",
        "raw": "",
        "timestamp": "",
    }


def test_real_backend_http_error_fails_loudly():
    session = FakeSplunkSession(FakeSplunkResponse("In handler export: forbidden", status_code=403))
    backend = _real_backend(session)
    with pytest.raises(SplunkSearchError, match="HTTP 403"):
        backend.search("sourcetype=linux_secure", earliest="all")


def test_real_backend_transport_error_is_wrapped():
    import requests

    backend = _real_backend(FakeSplunkSession(error=requests.Timeout("too slow")))
    with pytest.raises(SplunkSearchError, match="Splunk request failed"):
        backend.search("sourcetype=linux_secure", earliest="all")


def test_real_backend_execute_for_case_shape():
    csv_text = "_time,source,sourcetype,host,raw\n1758824043,s1,st1,h1,ev1\n"
    backend = _real_backend(FakeSplunkSession(FakeSplunkResponse(csv_text)))
    result = backend.execute_for_case(
        case_id="MOCK-CASE-001",
        rule_id="MOCK-RULE-001",
        query_title="Failed logins for user",
        spl="sourcetype=linux_secure action=failure user=bjones",
        earliest="-7d",
    )
    assert result["case_id"] == "MOCK-CASE-001"
    assert result["source_system"] == "splunk"
    payload = json.loads(result["raw_result"])
    assert payload["backend"] == "splunk"
    assert payload["row_count"] == len(payload["rows"]) == 1
    assert payload["rows"][0]["raw"] == "ev1"


def test_factory_selects_real_backend_when_configured(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.setenv("SPLUNK_URL", "https://splunk.example.com:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "test-token")
    backend = get_search_backend()
    assert isinstance(backend, RealSplunkBackend)
    assert backend._base_url == "https://splunk.example.com:8089"


# ---------------------------------------------------------------------------
# E.3: read-only saved-search listing (RealSplunkBackend.list_saved_searches)
# ---------------------------------------------------------------------------

_SAVED_SEARCH_PAYLOAD = {
    "entry": [
        {
            "name": "Zeta Failed Logins",
            "content": {
                "description": "failure spike",
                "search": "index=secure action=failure",
                "isvisible": True,
            },
        },
        {
            "name": "Alpha Windows Logons",
            "content": {
                "description": "",
                "search": "index=wineventlog EventCode=4624",
                "isvisible": True,
            },
        },
        {
            "name": "Hidden Search",
            "content": {
                "description": "internal only",
                "search": "index=internal",
                "isvisible": False,
            },
        },
        {
            "name": "Stringly Visible",
            "content": {"description": "", "search": "index=s", "isvisible": "0"},
        },
    ]
}


def test_saved_search_listing_request_shape():
    session = FakeSplunkSession(FakeSplunkResponse(json_data=_SAVED_SEARCH_PAYLOAD))
    backend = _real_backend(session)
    backend.list_saved_searches()

    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == "https://splunk.example.com:8089/services/saved/searches"
    assert call["headers"] == {"Authorization": "Bearer test-token"}
    assert call["params"] == {"count": "-1", "output_mode": "json"}
    assert call["timeout"] == 42.0


def test_saved_search_listing_filters_invisible_and_sorts():
    backend = _real_backend(
        FakeSplunkSession(FakeSplunkResponse(json_data=_SAVED_SEARCH_PAYLOAD))
    )
    items = backend.list_saved_searches()

    assert [item["name"] for item in items] == [
        "Alpha Windows Logons",
        "Zeta Failed Logins",
    ]
    assert items[1]["description"] == "failure spike"
    assert items[0]["search"] == "index=wineventlog EventCode=4624"


def test_saved_search_listing_caps_at_limit():
    backend = _real_backend(
        FakeSplunkSession(FakeSplunkResponse(json_data=_SAVED_SEARCH_PAYLOAD))
    )
    assert len(backend.list_saved_searches(limit=1)) == 1


def test_saved_search_listing_http_error_fails_loudly():
    backend = _real_backend(
        FakeSplunkSession(FakeSplunkResponse("In handler: forbidden", status_code=403))
    )
    with pytest.raises(SplunkSearchError, match="HTTP 403"):
        backend.list_saved_searches()


def test_saved_search_listing_rejects_non_json():
    backend = _real_backend(FakeSplunkSession(FakeSplunkResponse("<html>nope</html>")))
    with pytest.raises(SplunkSearchError, match="non-JSON"):
        backend.list_saved_searches()


# ---------------------------------------------------------------------------
# E.3: read-only backend introspection endpoints (api/routes/splunk.py)
# ---------------------------------------------------------------------------


def _mock_search_status(monkeypatch, **env):
    from api.routes.splunk import splunk_search_status

    for key in ("SEARCH_BACKEND", "SPLUNK_URL", "SPLUNK_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return splunk_search_status()


def test_search_status_mock_is_ready(monkeypatch):
    status = _mock_search_status(monkeypatch)
    assert status["backend"] == "mock"
    assert status["ready"] is True
    assert status["read_only"] is True
    assert status["problem"] == ""
    assert status["credentials"] == {
        "splunk_url_configured": False,
        "splunk_token_configured": False,
    }
    assert status["supported_spl_subset"]


def test_search_status_flags_unconfigured_splunk(monkeypatch):
    status = _mock_search_status(monkeypatch, SEARCH_BACKEND="splunk")
    assert status["ready"] is False
    assert "SPLUNK_URL" in status["problem"]


def test_search_status_reports_ready_when_configured(monkeypatch):
    status = _mock_search_status(
        monkeypatch,
        SEARCH_BACKEND="splunk",
        SPLUNK_URL="https://splunk.example.com:8089",
        SPLUNK_TOKEN="test-token",
    )
    assert status["ready"] is True
    assert status["problem"] == ""
    assert status["credentials"] == {
        "splunk_url_configured": True,
        "splunk_token_configured": True,
    }


def test_search_status_never_echoes_credential_values(monkeypatch):
    status = _mock_search_status(
        monkeypatch,
        SEARCH_BACKEND="splunk",
        SPLUNK_URL="https://very-secret-host.example:8089",
        SPLUNK_TOKEN="super-secret-bearer-value",
    )
    body = json.dumps(status)
    assert "very-secret-host" not in body
    assert "super-secret-bearer-value" not in body


def test_search_status_rejects_unknown_backend(monkeypatch):
    status = _mock_search_status(monkeypatch, SEARCH_BACKEND="elastic")
    assert status["ready"] is False
    assert "Unknown SEARCH_BACKEND" in status["problem"]


def test_saved_searches_endpoint_requires_real_backend(monkeypatch):
    from fastapi import HTTPException

    from api.routes.splunk import splunk_saved_searches

    monkeypatch.setenv("SEARCH_BACKEND", "mock")
    with pytest.raises(HTTPException) as excinfo:
        splunk_saved_searches()
    assert excinfo.value.status_code == 409
    assert "SEARCH_BACKEND=splunk" in excinfo.value.detail


def test_saved_searches_endpoint_unconfigured_is_503(monkeypatch):
    from fastapi import HTTPException

    from api.routes.splunk import splunk_saved_searches

    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.delenv("SPLUNK_URL", raising=False)
    monkeypatch.delenv("SPLUNK_TOKEN", raising=False)
    with pytest.raises(HTTPException) as excinfo:
        splunk_saved_searches()
    assert excinfo.value.status_code == 503
    assert "SPLUNK_URL" in excinfo.value.detail


def test_saved_searches_endpoint_returns_filtered_items(monkeypatch):
    from api.routes.splunk import splunk_saved_searches

    backend = _real_backend(
        FakeSplunkSession(FakeSplunkResponse(json_data=_SAVED_SEARCH_PAYLOAD))
    )
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    # The route imports the factory at call time, so patching the module
    # attribute is enough (no need to touch the router wiring).
    monkeypatch.setattr("services.search_backend.get_search_backend", lambda: backend)

    result = splunk_saved_searches()
    assert result["success"] is True
    assert result["count"] == 2
    assert [item["name"] for item in result["items"]] == [
        "Alpha Windows Logons",
        "Zeta Failed Logins",
    ]


def test_search_status_serves_over_http(monkeypatch):
    """End-to-end proof the new route is wired into the app (not just a fn)."""
    from fastapi.testclient import TestClient

    from api import auth
    from api.main import app

    # Disarm the key gate for this request only (tests run without .env), and
    # keep the app in API-key mode rather than session mode.
    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("SEARCH_BACKEND", raising=False)
    # No context manager: skip lifespan (no DB needed for this route).
    response = TestClient(app).get("/api/splunk/search-status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["backend"] == "mock"
    assert payload["ready"] is True
    assert payload["read_only"] is True


def test_saved_searches_rejects_mock_over_http(monkeypatch):
    from fastapi.testclient import TestClient

    from api import auth
    from api.main import app

    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "mock")
    response = TestClient(app).get("/api/splunk/saved-searches")

    assert response.status_code == 409
    assert "SEARCH_BACKEND=splunk" in response.json()["detail"]


# ---------------------------------------------------------------------------
# Real HTTP, no credentials: the connector against scripts/fake_splunk.py
#
# FakeSplunkSession (above) proves the connector's logic with a duck-typed
# session. These tests prove the same paths over an actual socket: requests
# serialization, bearer header, CSV parsing, epoch -> naive-UTC normalization,
# and HTTP error propagation — everything except Splunk's real contract, which
# only the live rehearsal (docs/SPLUNK_REHEARSAL.md) can verify.
# ---------------------------------------------------------------------------


def _load_fake_splunk_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "fake_splunk.py"
    spec = importlib.util.spec_from_file_location("soc_fake_splunk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fake_splunk(tmp_path, seed_events):
    module = _load_fake_splunk_module()
    server = module.FakeSplunkServer(
        token="fixture-token",
        seed_path=_write_seed(tmp_path, seed_events),
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_real_backend_over_http_returns_normalized_rows(fake_splunk):
    backend = RealSplunkBackend(base_url=fake_splunk.base_url, token="fixture-token", timeout=10)
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="-7d")

    assert len(rows) == 2
    assert rows[0]["host"].startswith("VPN-GW")
    assert "action=" in rows[0]["raw"]
    # The fake emits epoch seconds; the connector must normalize them back to
    # a naive-UTC ISO string (the platform's timestamp convention).
    parsed = datetime.fromisoformat(rows[0]["timestamp"])
    assert parsed.tzinfo is None
    assert parsed.year == datetime.now().year


def test_real_backend_over_http_rejects_bad_token(fake_splunk):
    backend = RealSplunkBackend(base_url=fake_splunk.base_url, token="wrong-token", timeout=10)
    with pytest.raises(SplunkSearchError, match="HTTP 401"):
        backend.search("sourcetype=linux_secure", earliest="-7d")


def test_real_backend_over_http_maps_unsupported_spl_to_http_error(fake_splunk):
    backend = RealSplunkBackend(base_url=fake_splunk.base_url, token="fixture-token", timeout=10)
    with pytest.raises(SplunkSearchError, match="HTTP 400"):
        backend.search("sourcetype=linux_secure | dedup host", earliest="-7d")


def test_real_backend_over_http_lists_only_visible_saved_searches(fake_splunk):
    backend = RealSplunkBackend(base_url=fake_splunk.base_url, token="fixture-token", timeout=10)
    items = backend.list_saved_searches()

    assert [item["name"] for item in items] == ["Failed Logons (Fake)"]
    assert "action=failure" in items[0]["search"]


def test_saved_searches_route_serves_from_fake_splunk(fake_splunk, monkeypatch):
    """End-to-end: env -> factory -> real HTTP -> route response."""
    from fastapi.testclient import TestClient

    from api import auth
    from api.main import app

    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.setenv("SPLUNK_URL", fake_splunk.base_url)
    monkeypatch.setenv("SPLUNK_TOKEN", "fixture-token")

    response = TestClient(app).get("/api/splunk/saved-searches")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["items"][0]["name"] == "Failed Logons (Fake)"


def test_connector_is_read_only_at_the_wire(fake_splunk):
    """`read_only` in /api/splunk/search-status is a claim; this proves it.

    The fake records every request it served, so a future connector change
    that reached for job creation, saved-search editing, or a DELETE would
    fail here even if it never appeared in a code review.
    """
    backend = RealSplunkBackend(base_url=fake_splunk.base_url, token="fixture-token", timeout=10)
    backend.search("sourcetype=linux_secure", earliest="-7d")
    backend.list_saved_searches()

    seen = [(r["method"], r["path"]) for r in fake_splunk.requests]
    assert seen == [
        ("POST", "/services/search/jobs/export"),
        ("GET", "/services/saved/searches"),
    ]
    # Nothing mutating and nothing outside the two read paths, even if the
    # connector grows a call later: the wire log is the whole story.
    assert all(method == "GET" or path.endswith("/jobs/export") for method, path in seen)
    assert {path for _, path in seen} <= {
        "/services/search/jobs/export",
        "/services/saved/searches",
    }


def test_csv_quoting_and_embedded_newlines_survive_the_wire(tmp_path):
    """Real Splunk CSV export quotes multi-line events; the connector reads
    them with `csv.DictReader`, not line splitting. This is the seam most
    likely to break on live data, so it runs over a real socket."""
    module = _load_fake_splunk_module()
    multi_line = (
        'sshd: action=failure user=bjones,\n    "quoted path", tail=1, host=VPN-GW-01'
    )
    seed = _write_seed(tmp_path, [{
        "source": "/var/log/secure",
        "sourcetype": "linux_secure",
        "host": "VPN-GW-01",
        "timestamp": (datetime.now() - timedelta(minutes=5)).isoformat(),
        "raw": multi_line,
    }])
    server = module.FakeSplunkServer(token="fixture-token", seed_path=seed).start()
    try:
        backend = RealSplunkBackend(base_url=server.base_url, token="fixture-token", timeout=10)
        rows = backend.search("sourcetype=linux_secure", earliest="-7d")
    finally:
        server.stop()

    assert len(rows) == 1
    assert rows[0]["raw"] == multi_line  # commas, quotes and the newline intact
    assert "\n" in rows[0]["raw"]
    # Epoch -> naive-UTC ISO, same instant the fixture described.
    parsed = datetime.fromisoformat(rows[0]["timestamp"])
    assert parsed.tzinfo is None
    assert abs((datetime.now() - parsed).total_seconds()) < 60 * 60


def test_connector_times_out_against_a_slow_splunk(tmp_path, seed_events):
    """A hanging Splunk must surface as a loud failure, not a stuck request."""
    module = _load_fake_splunk_module()
    server = module.FakeSplunkServer(
        token="fixture-token",
        seed_path=_write_seed(tmp_path, seed_events),
        delay_seconds=0.6,
    ).start()
    try:
        backend = RealSplunkBackend(base_url=server.base_url, token="fixture-token", timeout=0.2)
        with pytest.raises(SplunkSearchError, match="Splunk request failed"):
            backend.search("sourcetype=linux_secure", earliest="-7d")
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Route-level end-to-end: /api/splunk/search-one through the REAL connector
#
# The search-one tests in test_api_analyze_flow monkeypatch the factory to an
# inline mock backend. Here the route resolves RealSplunkBackend from env and
# the bytes actually cross a socket: env -> factory -> connector -> HTTP ->
# CSV parse -> ledger row. That was the last untested seam before the live
# rehearsal.
# ---------------------------------------------------------------------------

# `db_client` lives in conftest.py — shared with tests/test_demo_corpus.py.
def _seed_search_one_case(client, case_id="WIRE-CASE-1"):
    """A case + notable + playbook query — everything search-one resolves."""
    import db.models as db_models

    db = client.test_session()
    db.add(db_models.TriageResult(
        case_id=case_id,
        rule_name="Test Rule - Failed Logons",
        rule_id="test_rule",
        verdict="suspicious",
        confidence_score=0.5,
        analysis_summary="Synthetic case for the Splunk wire test.",
    ))
    db.add(db_models.SplunkEvent(
        source="pasted",
        sourcetype="splunk:notable:pasted",
        host="VPN-GW-01",
        raw=json.dumps({
            "promoted_case_id": case_id,
            "fields": {"host": "VPN-GW-01", "user": "bjones"},
        }),
    ))
    db.add(db_models.SupportiveQuery(
        rule_id="test_rule",
        title="Failed logons for user",
        description="",
        spl_query="sourcetype=linux_secure user=$user$",
    ))
    db.commit()
    db.close()
    return case_id


def _point_platform_at(monkeypatch, server, token="fixture-token"):
    """Configure the app exactly as a rehearsal would: env, not monkeypatching."""
    from api import auth

    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.setenv("SPLUNK_URL", server.base_url)
    monkeypatch.setenv("SPLUNK_TOKEN", token)


def test_search_one_route_runs_the_real_connector(db_client, fake_splunk, monkeypatch):
    import db.models as db_models

    _point_platform_at(monkeypatch, fake_splunk)
    case_id = _seed_search_one_case(db_client)

    response = db_client.post("/api/splunk/search-one", json={
        "case_id": case_id,
        "query_title": "Failed logons for user",
        "earliest": "-7d",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["backend"] == "RealSplunkBackend"
    assert body["result_status"] == "success"
    assert body["row_count"] >= 1
    assert body["spl"] == "sourcetype=linux_secure user=bjones"  # placeholder resolved
    assert "10.0.0.1" in " ".join(str(r.get("raw")) for r in body["rows"])
    assert body["error"] is None

    # The wire saw exactly one export POST, and the ledger recorded one row.
    assert [(r["method"], r["path"]) for r in fake_splunk.requests] == [
        ("POST", "/services/search/jobs/export")
    ]
    db = db_client.test_session()
    ledger = db.query(db_models.SupportiveQueryResult).filter(
        db_models.SupportiveQueryResult.case_id == case_id,
    ).all()
    tool_runs = db.query(db_models.ToolRun).all()
    db.close()
    assert [row.source_system for row in ledger] == ["splunk_auto"]
    assert json.loads(ledger[0].raw_result)["result_status"] == "success"
    assert len(tool_runs) == 1 and tool_runs[0].status == "completed"


def test_search_one_route_maps_splunk_403_to_query_failed(db_client, tmp_path, seed_events, monkeypatch):
    """The rehearsal's most likely bad day: a token without the search
    capability. The route must ledger a failed query, not 500."""
    import db.models as db_models

    module = _load_fake_splunk_module()
    server = module.FakeSplunkServer(
        token="fixture-token",
        seed_path=_write_seed(tmp_path, seed_events),
        fail_status=403,
        fail_body="In handler 'export': user lacks capability 'search'",
    ).start()
    try:
        _point_platform_at(monkeypatch, server)
        case_id = _seed_search_one_case(db_client)
        response = db_client.post("/api/splunk/search-one", json={
            "case_id": case_id,
            "query_title": "Failed logons for user",
            "earliest": "-7d",
        })
    finally:
        server.stop()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_status"] == "query_failed"
    assert body["row_count"] == 0
    assert "HTTP 403" in body["error"]
    assert "capability" in body["error"]  # Splunk's own message survives

    db = db_client.test_session()
    ledger = db.query(db_models.SupportiveQueryResult).filter(
        db_models.SupportiveQueryResult.case_id == case_id,
    ).all()
    tool_runs = db.query(db_models.ToolRun).all()
    db.close()
    assert json.loads(ledger[0].raw_result)["result_status"] == "query_failed"
    assert tool_runs[0].status == "failed"
    assert "HTTP 403" in (tool_runs[0].stderr or "")


def test_search_one_route_maps_empty_result_to_no_results(db_client, fake_splunk, monkeypatch):
    import db.models as db_models

    _point_platform_at(monkeypatch, fake_splunk)
    case_id = _seed_search_one_case(db_client)
    response = db_client.post("/api/splunk/search-one", json={
        "case_id": case_id,
        "query_title": "Failed logons for user",
        "spl": "sourcetype=linux_secure user=nosuchuser",
        "earliest": "-7d",
    })

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["row_count"] == 0
    assert body["result_status"] == "no_results"
    assert body["error"] is None

    db = db_client.test_session()
    ledger = db.query(db_models.SupportiveQueryResult).filter(
        db_models.SupportiveQueryResult.case_id == case_id,
    ).all()
    db.close()
    assert json.loads(ledger[0].raw_result)["result_status"] == "no_results"


def test_search_one_route_guardrail_times_out_a_slow_search(db_client, tmp_path, seed_events, monkeypatch):
    """A slow instance must not hold the case: the route's per-run guardrail
    fires even while the socket request is still in flight."""
    import api.routes.splunk as splunk_routes

    module = _load_fake_splunk_module()
    server = module.FakeSplunkServer(
        token="fixture-token",
        seed_path=_write_seed(tmp_path, seed_events),
        delay_seconds=0.8,
    ).start()
    try:
        _point_platform_at(monkeypatch, server)
        monkeypatch.setattr(splunk_routes, "_SEARCH_ONE_TIMEOUT_SECONDS", 0.2)
        case_id = _seed_search_one_case(db_client)
        response = db_client.post("/api/splunk/search-one", json={
            "case_id": case_id,
            "query_title": "Failed logons for user",
            "earliest": "-7d",
        })
    finally:
        server.stop()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["result_status"] == "query_failed"
    assert "timed out" in body["error"].lower()
    assert body["row_count"] == 0


# ---------------------------------------------------------------------------
# TLS trust path: SPLUNK_CA_BUNDLE (preferred) / SPLUNK_VERIFY_TLS (loud opt-out)
#
# Splunk management ports (8089) commonly serve an internal/self-signed cert.
# The rule this section pins down: trust can be *widened* for this one
# connection, and verification is on unless an operator says otherwise — an
# opt-out is never global and never silent.
# ---------------------------------------------------------------------------

def _clear_tls_env(monkeypatch):
    monkeypatch.delenv("SPLUNK_CA_BUNDLE", raising=False)
    monkeypatch.delenv("SPLUNK_VERIFY_TLS", raising=False)


def test_ca_bundle_env_is_used_for_verification(monkeypatch, tmp_path):
    _clear_tls_env(monkeypatch)
    bundle = tmp_path / "splunk-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(bundle))

    session = FakeSplunkSession(FakeSplunkResponse("_time,raw\n"))
    backend = _real_backend(session)
    backend.search("sourcetype=linux_secure")

    assert session.calls[0]["verify"] == str(bundle)


def test_missing_ca_bundle_fails_at_construction(monkeypatch, tmp_path):
    _clear_tls_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(tmp_path / "nope.pem"))

    with pytest.raises(SplunkSearchError, match="SPLUNK_CA_BUNDLE"):
        _real_backend(FakeSplunkSession(FakeSplunkResponse("_time\n")))


def test_verify_tls_false_disables_verification_per_connection(monkeypatch):
    _clear_tls_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_VERIFY_TLS", "false")

    session = FakeSplunkSession(FakeSplunkResponse("_time,raw\n"))
    backend = _real_backend(session)
    backend.search("sourcetype=linux_secure")

    assert session.calls[0]["verify"] is False


def test_ca_bundle_wins_over_verify_tls_false(monkeypatch, tmp_path):
    """Contradictory config resolves toward verification, never away from it."""
    _clear_tls_env(monkeypatch)
    bundle = tmp_path / "splunk-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(bundle))
    monkeypatch.setenv("SPLUNK_VERIFY_TLS", "false")

    session = FakeSplunkSession(FakeSplunkResponse("_time,raw\n"))
    _real_backend(session).search("sourcetype=linux_secure")

    assert session.calls[0]["verify"] == str(bundle)


def test_tls_settings_reports_posture(monkeypatch, tmp_path):
    from services.search_backend import tls_settings

    _clear_tls_env(monkeypatch)
    setting = tls_settings()
    assert setting["verify"] is True
    assert setting["verification_disabled"] is False
    assert setting["ca_bundle_configured"] is False
    assert setting["ca_bundle_certificates"] == 0
    assert setting["ca_bundle_expires_at"] is None
    assert setting["ca_bundle_days_remaining"] is None
    assert setting["ca_bundle_warning"] == ""
    assert setting["problem"] == ""

    bundle, _key = _self_signed_cert(tmp_path, days_valid=365)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(bundle))
    setting = tls_settings()
    assert setting["verify"] is True
    assert setting["ca_bundle_configured"] is True
    assert setting["ca_bundle_readable"] is True
    assert setting["ca_bundle_certificates"] == 1
    assert setting["ca_bundle_expired"] is False
    assert setting["ca_bundle_days_remaining"] >= 360
    assert setting["ca_bundle_warning"] == ""
    assert setting["problem"] == ""

    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(tmp_path / "gone.pem"))
    assert "does not exist" in tls_settings()["problem"]

    monkeypatch.delenv("SPLUNK_CA_BUNDLE")
    monkeypatch.setenv("SPLUNK_VERIFY_TLS", "0")
    setting = tls_settings()
    assert setting["verify"] is False
    assert setting["verification_disabled"] is True


def test_search_status_reports_tls_posture(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from api import auth
    from api.main import app

    _clear_tls_env(monkeypatch)
    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.setenv("SPLUNK_URL", "https://splunk.example.com:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "fixture-token")

    payload = TestClient(app).get("/api/splunk/search-status").json()
    assert payload["ready"] is True
    tls = payload["tls"]
    assert tls["verify"] is True
    assert tls["verification_disabled"] is False
    assert tls["ca_bundle_configured"] is False
    assert tls["ca_bundle_readable"] is False
    # No bundle means nothing to expire: the system trust store is the source.
    assert tls["ca_bundle_expires_at"] is None
    assert tls["ca_bundle_days_remaining"] is None
    assert tls["ca_bundle_expired"] is False
    assert tls["ca_bundle_warning"] == ""

    # A bundle path that isn't there is a readiness failure, named up front.
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(tmp_path / "gone.pem"))
    payload = TestClient(app).get("/api/splunk/search-status").json()
    assert payload["ready"] is False
    assert "SPLUNK_CA_BUNDLE" in payload["problem"]


def _status_payload(monkeypatch):
    """search-status as an operator sees it, with the key gate disarmed."""
    from fastapi.testclient import TestClient

    from api import auth
    from api.main import app

    monkeypatch.setattr(auth, "_API_KEY", "")
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "splunk")
    monkeypatch.setenv("SPLUNK_URL", "https://splunk.example.com:8089")
    monkeypatch.setenv("SPLUNK_TOKEN", "fixture-token")
    return TestClient(app).get("/api/splunk/search-status").json()


def test_expiring_bundle_warns_without_failing_readiness(monkeypatch, tmp_path):
    """Usable but close to breaking: green readiness, loud warning."""
    _clear_tls_env(monkeypatch)
    cert_path, _key = _self_signed_cert(tmp_path, days_valid=10)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(cert_path))

    payload = _status_payload(monkeypatch)
    tls = payload["tls"]

    assert payload["ready"] is True          # still works today
    assert payload["problem"] == ""
    assert tls["ca_bundle_certificates"] == 1
    assert tls["ca_bundle_expired"] is False
    assert 0 <= tls["ca_bundle_days_remaining"] <= 10
    assert tls["ca_bundle_expires_at"].startswith(str(datetime.now().year))
    assert "expires in" in tls["ca_bundle_warning"]
    assert "rotate" in tls["ca_bundle_warning"]


def test_expired_bundle_fails_readiness_before_a_search_runs(monkeypatch, tmp_path):
    """The whole point: discovery at status time, not at handshake time."""
    _clear_tls_env(monkeypatch)
    cert_path, _key = _self_signed_cert(tmp_path, not_after_days=-1)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(cert_path))

    payload = _status_payload(monkeypatch)
    tls = payload["tls"]

    assert payload["ready"] is False
    assert "expired" in payload["problem"].lower()
    assert tls["ca_bundle_expired"] is True
    assert tls["ca_bundle_days_remaining"] < 0
    # Verification is still ON — the bundle is stale, not disabled.
    assert tls["verify"] is True


def test_bundle_without_certificates_is_a_readiness_problem(monkeypatch, tmp_path):
    _clear_tls_env(monkeypatch)
    bundle = tmp_path / "not-a-cert.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\nnot base64\n", encoding="utf-8")
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(bundle))

    payload = _status_payload(monkeypatch)
    assert payload["ready"] is False
    assert "no parseable PEM certificate" in payload["problem"]
    assert payload["tls"]["ca_bundle_expires_at"] is None


def test_directory_bundle_reports_the_earliest_expiry(monkeypatch, tmp_path):
    """A bundle directory is only as good as its soonest-expiring member.

    Uses OpenSSL's hashed naming (`9f86d081.0`), the shape urllib3 actually
    expects for a directory `verify=` value — a suffix filter would miss it.
    """
    from services.search_backend import tls_settings

    _clear_tls_env(monkeypatch)
    cas = tmp_path / "cas"
    cas.mkdir()
    _self_signed_cert(cas, days_valid=365, filename="9f86d081.0")
    _self_signed_cert(cas, days_valid=20, filename="1b4f0e98.1")
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", str(cas))

    setting = tls_settings()
    assert setting["ca_bundle_certificates"] == 2
    assert setting["ca_bundle_expired"] is False
    assert setting["ca_bundle_days_remaining"] <= 20  # the rotating one wins
    assert "expires in" in setting["ca_bundle_warning"]
    assert setting["problem"] == ""


def _self_signed_cert(tmp_path, days_valid=1, filename="fake-splunk-cert.pem", not_after_days=None):
    """Throwaway cert for 127.0.0.1, generated per test.

    Generated rather than committed: a private key in the repo is a finding,
    even a fake one. `cryptography` is already a pinned runtime dependency.

    `days_valid` moves the expiry into the future; `not_after_days` (negative)
    puts it in the past for the expired-bundle cases.
    """
    import ipaddress
    from datetime import timezone as _tz

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(_tz.utc)
    not_after = (
        now + timedelta(days=not_after_days) if not_after_days is not None
        else now + timedelta(days=days_valid)
    )
    not_before = (
        now - timedelta(days=abs(not_after_days) + 10) if not_after_days is not None
        else now - timedelta(minutes=5)
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fake-splunk-test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / filename
    key_path = tmp_path / (filename.rsplit(".", 1)[0] + "-key.pem")
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return cert_path, key_path


@pytest.fixture()
def tls_splunk(tmp_path, seed_events):
    module = _load_fake_splunk_module()
    cert_path, key_path = _self_signed_cert(tmp_path)
    server = module.FakeSplunkServer(
        token="fixture-token",
        seed_path=_write_seed(tmp_path, seed_events),
        tls_cert=str(cert_path),
        tls_key=str(key_path),
    ).start()
    server.cert_path = str(cert_path)
    try:
        yield server
    finally:
        server.stop()


def test_tls_handshake_without_trust_fails_with_the_fix_named(tls_splunk, monkeypatch):
    """A bare handshake against a self-signed port must fail, and the error
    must name SPLUNK_CA_BUNDLE — not just CERTIFICATE_VERIFY_FAILED."""
    _clear_tls_env(monkeypatch)
    assert tls_splunk.base_url.startswith("https://")

    backend = RealSplunkBackend(base_url=tls_splunk.base_url, token="fixture-token", timeout=10)
    with pytest.raises(SplunkSearchError) as excinfo:
        backend.search("sourcetype=linux_secure", earliest="-7d")

    message = str(excinfo.value)
    assert "TLS verification failed" in message
    assert "SPLUNK_CA_BUNDLE" in message


def test_tls_handshake_succeeds_with_ca_bundle(tls_splunk, monkeypatch):
    """The supported path: pin the instance's CA, keep verification on."""
    _clear_tls_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", tls_splunk.cert_path)

    backend = RealSplunkBackend(base_url=tls_splunk.base_url, token="fixture-token", timeout=10)
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="-7d")

    assert backend._verify == tls_splunk.cert_path
    assert len(rows) == 2
    assert rows[0]["host"].startswith("VPN-GW")


def test_tls_handshake_succeeds_with_explicit_opt_out(tls_splunk, monkeypatch):
    """The escape hatch still works when there is no bundle to pin."""
    _clear_tls_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_VERIFY_TLS", "false")

    backend = RealSplunkBackend(base_url=tls_splunk.base_url, token="fixture-token", timeout=10)
    rows = backend.search("sourcetype=linux_secure user=bjones", earliest="-7d")

    assert backend._verify is False
    assert len(rows) == 2


def test_saved_searches_over_tls_with_ca_bundle(tls_splunk, monkeypatch):
    _clear_tls_env(monkeypatch)
    monkeypatch.setenv("SPLUNK_CA_BUNDLE", tls_splunk.cert_path)

    backend = RealSplunkBackend(base_url=tls_splunk.base_url, token="fixture-token", timeout=10)
    items = backend.list_saved_searches()

    assert [item["name"] for item in items] == ["Failed Logons (Fake)"]
