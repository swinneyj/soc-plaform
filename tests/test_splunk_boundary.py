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
    """Stub the boundary's canonical CSV engine so unit tests never touch Postgres."""
    calls = []

    def fake_ingest_csv(path, silent=True, session_factory=None):
        calls.append(path)
        return {
            "success": True,
            "rows_read": rows_inserted,
            "rows_inserted": rows_inserted,
            "rows_skipped": 0,
            "errors": [],
            "file": os.path.basename(str(path)),
        }

    monkeypatch.setattr(sb, "ingest_csv_events", fake_ingest_csv)
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


def test_purge_prefers_ids_over_window(tmp_path, monkeypatch):
    """Manifests with inserted_ids must purge by exact ids — ingest-time
    windows can overlap between rapid consecutive batches, so window-based
    deletion could eat a neighboring batch's rows."""
    monkeypatch.setattr(sb, "_delete_events_by_ids", lambda ids: len(ids))
    staging = sb.staging_dir()
    batch_id = "20260925T120000Z-abcdef01"
    (staging / f"{batch_id}-data.csv").write_text("x", encoding="utf-8")
    (staging / f"{batch_id}-manifest.json").write_text(json.dumps({
        "batch_id": batch_id,
        "staged_path": str(staging / f"{batch_id}-data.csv"),
        "inserted_ids": [11, 22],
        "ingest_window": ["2026-09-25T12:00:00", "2026-09-25T12:10:00"],
    }), encoding="utf-8")
    result = sb.purge_batch(batch_id)
    assert result["purge_strategy"] == "ids"
    assert result["events_deleted"] == 2


def test_purge_window_fallback_for_legacy_manifest(tmp_path, monkeypatch):
    """Pre-ids manifests (no inserted_ids) still purge via their window."""
    monkeypatch.setattr(sb, "_delete_events_in_window", lambda window: 5)
    staging = sb.staging_dir()
    batch_id = "20260925T120000Z-abcdef02"
    (staging / f"{batch_id}-manifest.json").write_text(json.dumps({
        "batch_id": batch_id,
        "staged_path": str(staging / f"{batch_id}-data.csv"),
        "ingest_window": ["2026-09-25T12:00:00", "2026-09-25T12:10:00"],
    }), encoding="utf-8")
    result = sb.purge_batch(batch_id)
    assert result["purge_strategy"] == "window"
    assert result["events_deleted"] == 5


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


def test_staging_dir_falls_back_when_repo_readonly(tmp_path, monkeypatch):
    """Serverless deploys (Vercel) have a read-only app tree; staging must
    fall back to a temp location instead of 500ing (seen live as
    '[Errno 30] Read-only file system: /var/task/Data')."""
    import tempfile as tf

    readonly = tmp_path / "readonly_repo"
    (readonly / "Data").mkdir(parents=True)
    monkeypatch.setattr(
        "services.splunk_boundary.Path", lambda p: readonly / p
    )
    # Simulate the mkdir/touch failure the way the read-only FS does:
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", "")
    orig_mkdir = __import__("pathlib").Path.mkdir

    def failing_mkdir(self, *a, **k):
        if str(self).startswith(str(readonly)):
            raise OSError(30, "Read-only file system")
        return orig_mkdir(self, *a, **k)

    monkeypatch.setattr("pathlib.Path.mkdir", failing_mkdir)
    staging = sb.staging_dir()
    assert str(tf.gettempdir()) in str(staging) or staging.is_dir()


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


def test_http_write_endpoints_require_api_key(monkeypatch):
    """The purge/admit HTTP surface sits behind the API-key gate.

    With API_KEY configured, unauthenticated writes must 401 before any
    handler logic runs. (With no key configured the gate is a no-op for
    local dev — covered elsewhere.)
    """
    from api import main as api_main

    monkeypatch.setattr(api_main, "_API_KEY", "test-secret-key")
    client = _client()
    admit = client.post(
        "/api/splunk-boundary/admit",
        files={"file": ("x.csv", io.BytesIO(b"a,b\n1,2\n"), "text/csv")},
    )
    purge = client.delete("/api/splunk-boundary/batches/20260925T120000Z-abcdef01")
    assert admit.status_code == 401
    assert purge.status_code == 401


# ---------------------------------------------------------------------------
# Canonical CSV engine (ingest_csv_events) — sqlite-backed, hermetic
# ---------------------------------------------------------------------------


def _sqlite_session_factory():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from db.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    return factory


def test_ingest_csv_events_inserts_and_dedups(tmp_path):
    from db.models import SplunkEvent

    factory = _sqlite_session_factory()
    path = tmp_path / "events.csv"
    path.write_text(
        "_time,host,source,sourcetype,_raw\n"
        "2026-09-25T10:00:00,H1,/v/log,linux_secure,hello world\n"
        "2026-09-25T10:01:00,H2,/v/log,linux_secure,second event\n",
        encoding="utf-8",
    )

    first = sb.ingest_csv_events(path, session_factory=factory)
    assert first["success"] is True
    assert first["rows_inserted"] == 2

    session = factory()
    assert session.query(SplunkEvent).count() == 2
    event = session.query(SplunkEvent).first()
    assert event.raw == "hello world"
    session.close()

    # Re-ingesting the same file must skip everything as duplicates.
    second = sb.ingest_csv_events(path, session_factory=factory)
    assert second["rows_inserted"] == 0
    assert second["rows_skipped"] == 2


def test_ingest_csv_events_normalizes_tz_and_defaults(tmp_path):
    from db.models import SplunkEvent

    factory = _sqlite_session_factory()
    path = tmp_path / "events.csv"
    path.write_text(
        "host,_raw\n"
        "H9,no timestamp and no sourcetype\n",
        encoding="utf-8",
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    assert stats["rows_inserted"] == 1

    session = factory()
    event = session.query(SplunkEvent).one()
    assert event.sourcetype == "splunk:notable"  # default applied
    assert event.source == "splunk_export"
    assert event.timestamp.tzinfo is None  # naive UTC
    session.close()


def test_ingest_csv_events_missing_file(tmp_path):
    stats = sb.ingest_csv_events(tmp_path / "nope.csv", session_factory=_sqlite_session_factory())
    assert stats["success"] is False
    assert "not found" in stats["error"]


# ---------------------------------------------------------------------------
# JSON notable admission (ingest_json_notables)
# ---------------------------------------------------------------------------


def test_ingest_json_notables_inserts_object_and_array(tmp_path):
    from db.models import SplunkEvent

    factory = _sqlite_session_factory()
    single = tmp_path / "one.json"
    single.write_text(json.dumps({
        "search_name": "Impossible Travel",
        "user": "bjones",
        "host": "HOST-1",
        "_time": "2026-09-25T10:00:00Z",
    }), encoding="utf-8")
    stats = sb.ingest_json_notables(single, session_factory=factory)
    assert stats["success"] is True
    assert stats["rows_inserted"] == 1

    many = tmp_path / "many.json"
    many.write_text(json.dumps([
        {"search_name": "Malware", "host": "HOST-2", "_time": 1758792000.0},
        {"search_name": "Exfil", "host": "HOST-3"},
    ]), encoding="utf-8")
    stats2 = sb.ingest_json_notables(many, session_factory=factory)
    assert stats2["rows_inserted"] == 2

    session = factory()
    assert session.query(SplunkEvent).filter(SplunkEvent.source == "splunk_json_export").count() == 3
    epoch_event = session.query(SplunkEvent).filter(SplunkEvent.host == "HOST-2").one()
    assert epoch_event.timestamp.tzinfo is None  # epoch normalized to naive UTC
    session.close()


def test_ingest_json_notables_dedups_by_payload(tmp_path):
    """Same-second events with different payloads must BOTH insert; exact
    re-admission of the same payload must skip."""
    from db.models import SplunkEvent

    factory = _sqlite_session_factory()
    path = tmp_path / "events.json"
    base = {"host": "H1", "_time": "2026-09-25T10:00:00Z", "sourcetype": "splunk:notable"}
    path.write_text(json.dumps([
        dict(base, search_name="Rule A"),
        dict(base, search_name="Rule B"),   # same second, different payload
    ]), encoding="utf-8")
    first = sb.ingest_json_notables(path, session_factory=factory)
    assert first["rows_inserted"] == 2

    second = sb.ingest_json_notables(path, session_factory=factory)
    assert second["rows_inserted"] == 0
    assert second["rows_skipped"] == 2  # payload-hash dedup

    session = factory()
    assert session.query(SplunkEvent).count() == 2
    session.close()


def test_ingest_json_notables_rejects_malformed(tmp_path):
    factory = _sqlite_session_factory()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    stats = sb.ingest_json_notables(bad, session_factory=factory)
    assert stats["success"] is False
    assert "invalid JSON" in stats["error"]

    scalar = tmp_path / "scalar.json"
    scalar.write_text("[1, 2, 3]", encoding="utf-8")
    stats2 = sb.ingest_json_notables(scalar, session_factory=factory)
    assert stats2["success"] is False
    assert "array of objects" in stats2["error"]


def test_admit_json_file_end_to_end(tmp_path, monkeypatch):
    """A .json admission produces a batch manifest and inserts rows."""
    from db.models import SplunkEvent

    factory = _sqlite_session_factory()
    monkeypatch.setattr(sb, "_load_db_module", lambda: (SplunkEvent, factory))

    path = tmp_path / "notables.json"
    path.write_text(json.dumps([
        {"search_name": "R1", "host": "JH1", "_time": "2026-09-25T11:00:00Z"},
        {"search_name": "R2", "host": "JH2", "_time": "2026-09-25T11:01:00Z"},
    ]), encoding="utf-8")
    result = sb.admit_file(path, source_label="unit-test-json")
    assert result["ingest"]["rows_inserted"] == 2
    assert result["manifest"]["validation"]["filename"] == "notables.json"
    staging = sb.staging_dir()
    assert list(staging.glob("*-notables.json"))

    session = factory()
    assert session.query(SplunkEvent).filter(SplunkEvent.source == "splunk_json_export").count() == 2
    session.close()
