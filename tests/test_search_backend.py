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
    row = backend.search("sourcetype=linux_secure user=asmith", earliest="all")[0]
    assert set(row.keys()) == {"source", "sourcetype", "host", "timestamp", "raw"}


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
    # (api.main._rescope_variant_spl); it must win over the job-level window.
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
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code


class FakeSplunkSession:
    """Duck-typed requests.Session: records the request, returns a canned reply."""

    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
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
