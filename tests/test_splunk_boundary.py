"""Tests for the Splunk boundary ("the latch").

Hermetic: every test isolates staging via SPLUNK_BOUNDARY_STAGING and, where
the database is touched, via a monkeypatched ingest window. No Postgres
required for the boundary logic itself.
"""

import io
import json
import os

import pytest

from services import splunk_boundary as sb


@pytest.fixture(autouse=True)
def isolated_staging(tmp_path, monkeypatch):
    staging = tmp_path / "quarantine"
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(staging))
    monkeypatch.delenv("SPLUNK_BOUNDARY_MODE", raising=False)
    return staging


def _make_csv(tmp_path, name="events.csv", rows=None):
    path = tmp_path / name
    rows = rows or [
        {"_time": "2026-09-25 10:00:00", "host": "H1", "source": "/v/log", "raw": "line one"},
        {"_time": "2026-09-25 10:05:00", "host": "H2", "source": "/v/log", "raw": "line two"},
    ]
    import csv

    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def _fake_ingest(monkeypatch, rows_inserted=2):
    calls = []

    def fake_ingest(path, silent=False):
        calls.append(path)
        return {
            "success": True,
            "rows_read": rows_inserted,
            "rows_inserted": rows_inserted,
            "rows_skipped": 0,
            "errors": [],
            "file": str(path),
        }

    monkeypatch.setattr(
        "services.splunk_boundary.ingest_splunk_csv", fake_ingest, raising=False
    )
    # The real import happens inside ingest_manifest; patch sys.modules path
    import sys as _sys
    import types as _types

    fake_mod = _types.ModuleType("Tools.splunk_csv_ingestor.splunk_csv_ingestor")
    fake_mod.ingest_splunk_csv = fake_ingest
    monkeypatch.setitem(_sys.modules, "Tools.splunk_csv_ingestor.splunk_csv_ingestor", fake_mod)
    return calls


# ---------------------------------------------------------------------------
# Validation (fail-closed)
# ---------------------------------------------------------------------------


def test_validate_accepts_clean_csv(tmp_path):
    path = _make_csv(tmp_path)
    report = sb.validate_file(path)
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["filename"] == path.name


def test_validate_rejects_disallowed_extension(tmp_path):
    path = tmp_path / "payload.exe"
    path.write_bytes(b"MZ\x90\x00")
    report = sb.validate_file(path)
    assert report["ok"] is False
    assert any("allowlist" in v for v in report["violations"])


def test_validate_rejects_binary_content_with_allowed_extension(tmp_path):
    path = tmp_path / "sneaky.csv"
    path.write_bytes(b"\x00\x01\x02" * 100)
    report = sb.validate_file(path)
    assert report["ok"] is False
    assert any("binary" in v for v in report["violations"])


def test_validate_rejects_oversized_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SPLUNK_BOUNDARY_MAX_BYTES", "10")
    path = _make_csv(tmp_path)
    report = sb.validate_file(path)
    assert report["ok"] is False
    assert any("cap" in v for v in report["violations"])


def test_validate_uses_basename_only(tmp_path):
    # Even a traversal-looking path must only ever contribute its basename.
    tricky = tmp_path / "sub"
    tricky.mkdir()
    path = _make_csv(tricky, name="..dots.csv")
    report = sb.validate_file(path)
    assert report["filename"] == "..dots.csv"


def test_validate_missing_file(tmp_path):
    report = sb.validate_file(tmp_path / "nope.csv")
    assert report["ok"] is False


# ---------------------------------------------------------------------------
# Mode latch
# ---------------------------------------------------------------------------


def test_default_mode_is_quarantined():
    assert sb.current_mode() == "quarantined"


def test_mode_can_be_set(monkeypatch):
    monkeypatch.setenv("SPLUNK_BOUNDARY_MODE", "restricted")
    assert sb.current_mode() == "restricted"


def test_invalid_mode_raises(monkeypatch):
    monkeypatch.setenv("SPLUNK_BOUNDARY_MODE", "yolo")
    with pytest.raises(ValueError, match="Invalid SPLUNK_BOUNDARY_MODE"):
        sb.current_mode()


# ---------------------------------------------------------------------------
# Quarantine / admit / purge
# ---------------------------------------------------------------------------


def test_quarantine_stages_copy_and_manifest(tmp_path):
    path = _make_csv(tmp_path)
    manifest = sb.quarantine_file(path, source_label="test")
    staging = sb.staging_dir()
    staged = list(staging.glob("*-events.csv"))
    assert len(staged) == 1
    assert staged[0].is_file()
    assert (staging / f"{manifest['batch_id']}-manifest.json").is_file()
    # original untouched (copy, not move)
    assert path.is_file()


def test_quarantine_refuses_invalid_file(tmp_path):
    path = tmp_path / "bad.exe"
    path.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="validation failed"):
        sb.quarantine_file(path)


def test_admit_file_end_to_end(tmp_path, monkeypatch):
    calls = _fake_ingest(monkeypatch)
    path = _make_csv(tmp_path)
    result = sb.admit_file(path, source_label="unit-test")
    assert len(calls) == 1
    assert result["ingest"]["rows_inserted"] == 2
    assert result["manifest"]["mode"] == "quarantined"
    assert "ingest_window" in result["manifest"]


def test_purge_batch_removes_staging_and_reports(tmp_path, monkeypatch):
    _fake_ingest(monkeypatch)
    monkeypatch.setattr(sb, "_delete_events_in_window", lambda window: 0)
    path = _make_csv(tmp_path)
    result = sb.admit_file(path)
    batch_id = result["manifest"]["batch_id"]

    purge = sb.purge_batch(batch_id)
    assert purge["batch_id"] == batch_id
    assert purge["events_deleted"] == 0
    staging = sb.staging_dir()
    remaining = list(staging.glob(f"{batch_id}*"))
    assert remaining == []


def test_purge_rejects_malformed_batch_id():
    with pytest.raises(ValueError, match="malformed"):
        sb.purge_batch("../../etc/passwd")


def test_list_batches_roundtrip(tmp_path, monkeypatch):
    _fake_ingest(monkeypatch)
    sb.admit_file(_make_csv(tmp_path, name="one.csv"))
    sb.admit_file(_make_csv(tmp_path, name="two.csv"))
    batches = sb.list_batches()
    assert len(batches) == 2


def test_status_reports_batches(tmp_path, monkeypatch):
    _fake_ingest(monkeypatch)
    sb.admit_file(_make_csv(tmp_path))
    status = sb.status()
    assert status["mode"] == "quarantined"
    assert status["batch_count"] == 1
    assert status["batches"][0]["ingested_rows"] == 2


# ---------------------------------------------------------------------------
# HTTP surface behavior
# ---------------------------------------------------------------------------


def _client():
    from fastapi.testclient import TestClient
    import api.main as api_main

    return TestClient(api_main.app)


def test_http_admission_blocked_in_quarantined_mode():
    resp = _client().post(
        "/api/splunk-boundary/admit",
        files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    assert resp.status_code == 403
    assert "quarantined" in resp.json()["detail"]


def test_http_admission_allowed_in_restricted_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SPLUNK_BOUNDARY_MODE", "restricted")
    _fake_ingest(monkeypatch)
    resp = _client().post(
        "/api/splunk-boundary/admit",
        files={"file": ("events.csv", io.BytesIO(b"_time,host\n2026-09-25,H1\n"), "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["ingest"]["rows_inserted"] >= 1


def test_http_status_endpoint():
    resp = _client().get("/api/splunk-boundary/status")
    assert resp.status_code == 200
    assert "mode" in resp.json()


def test_http_purge_rejects_malformed_id():
    resp = _client().delete("/api/splunk-boundary/batches/not-a-batch")
    assert resp.status_code == 400
