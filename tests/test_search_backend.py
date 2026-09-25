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
    get_search_backend,
)


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


def test_real_backend_fails_loudly():
    with pytest.raises(NotImplementedError, match="not wired yet"):
        RealSplunkBackend()
