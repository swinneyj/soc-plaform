"""Adversarial fuzz suite for the Splunk boundary ingest ("the latch").

Feeds the two canonical engines — ingest_csv_events and ingest_json_notables
— the kind of messy real-world variance a genuine Splunk export produces,
hermetically (sqlite via session_factory, isolated staging via the autouse
fixture pattern from test_splunk_boundary.py).

For every case the contract is the same:
  * the call must never raise — bad input degrades to stats/error strings;
  * stats must stay self-consistent (read == inserted + skipped; ids match);
  * whatever lands in the DB must be bounded (raw <= 2000 chars) and
    deduplicated on re-ingest;
  * nothing may crash the caller or corrupt the session for later rows.

The cases double as a survive/skip/fail report: each test name states what
real-world variance it models, so a failure reads as an ingest-tolerance
finding, not an abstract unit-test break.
"""

import json
import os
from pathlib import Path

import pytest

from services import splunk_boundary as sb


@pytest.fixture(autouse=True)
def isolated_staging(tmp_path, monkeypatch):
    """Point the quarantine dir at tmp for every fuzz test — the suite
    must never touch (or litter, on failure) the real Data/quarantine."""
    staging = tmp_path / "quarantine"
    monkeypatch.setenv("SPLUNK_BOUNDARY_STAGING", str(staging))
    monkeypatch.delenv("SPLUNK_BOUNDARY_MODE", raising=False)
    return staging


@pytest.fixture()
def factory():
    """Isolated sqlite-backed SplunkEvent store, fresh per test."""
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
    return sessionmaker(bind=engine, autoflush=False)


def _assert_safe_stats(stats):
    """Every ingest result, good or bad, must obey the stats contract."""
    assert isinstance(stats, dict), "ingest must always return a stats dict"
    for key in ("rows_read", "rows_inserted", "rows_skipped"):
        assert key in stats, f"stats missing {key}: {stats}"
    if stats.get("success") and not stats.get("error"):
        assert stats["rows_read"] == stats["rows_inserted"] + stats["rows_skipped"], stats
    assert stats["rows_inserted"] == len(stats.get("inserted_ids") or []), stats
    assert stats["rows_read"] >= stats["rows_inserted"], stats


def _stored(factory):
    from db.models import SplunkEvent

    session = factory()
    try:
        return session.query(SplunkEvent).order_by(SplunkEvent.id).all()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CSV variance
# ---------------------------------------------------------------------------


def test_fuzz_csv_utf8_bom_crlf_quoted(tmp_path, factory):
    """Windows-flavored export: BOM, CRLF endings, quoted fields with commas."""
    path = tmp_path / "bom.csv"
    path.write_bytes(
        b"\xef\xbb\xbf_time,host,source,sourcetype,_raw\r\n"
        b'"2026-09-25T10:00:00","host, west","/var/log/auth.log","linux_secure",'
        b'"Failed password for admin from 203.0.113.9 port 22"\r\n'
        b'"2026-09-25T10:01:00","host, east","/var/log/auth.log","linux_secure",'
        b'"session opened for user smoke_user"\r\n'
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 2, stats
    events = _stored(factory)
    assert any("host, west" in (e.host or "") for e in events)
    assert any("203.0.113.9" in (e.raw or "") for e in events)


def test_fuzz_csv_latin1_bytes(tmp_path, factory):
    """Legacy latin-1 export (0xE9 in text) must not explode the reader."""
    path = tmp_path / "latin1.csv"
    path.write_bytes(
        b"_time,host,source,sourcetype,_raw\n"
        b"2026-09-25T10:00:00,H1,/v/log,linux_secure,caf\xe9 owner smoke_user\n"
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 1, stats


def test_fuzz_csv_multiline_raw_field(tmp_path, factory):
    """Embedded newlines inside quoted _raw (multi-line stack traces)."""
    path = tmp_path / "multiline.csv"
    path.write_text(
        '_time,host,source,sourcetype,_raw\n'
        '2026-09-25T10:00:00,H1,/v/app,app_log,"line one\nline two\nline three"\n'
        '2026-09-25T10:01:00,H2,/v/app,app_log,"plain"\n',
        encoding="utf-8",
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 2, stats
    events = _stored(factory)
    assert any("\n" in (e.raw or "") for e in events), "multiline raw must survive"


def test_fuzz_csv_duplicate_timestamps_dedup(tmp_path, factory):
    """Same-second duplicates: in-file and across re-ingest runs."""
    path = tmp_path / "dupes.csv"
    body = (
        "_time,host,source,sourcetype,_raw\n"
        "2026-09-25T10:00:00,H1,/v/log,linux_secure,alpha\n"
        "2026-09-25T10:00:00,H1,/v/log,linux_secure,alpha\n"
        "2026-09-25T10:00:00,H1,/v/log,linux_secure,alpha\n"
        "2026-09-25T10:00:00,H2,/v/log,linux_secure,beta\n"
    )
    path.write_text(body, encoding="utf-8")

    first = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(first)
    assert first["rows_inserted"] == 2 and first["rows_skipped"] == 2, first

    second = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(second)
    assert second["rows_inserted"] == 0 and second["rows_skipped"] == 4, second
    assert len(_stored(factory)) == 2, "re-ingest must not create duplicates"


def test_fuzz_csv_messy_timestamps(tmp_path, factory):
    """Epoch floats, ISO-Z, Splunk's default format, and pure garbage."""
    path = tmp_path / "timestamps.csv"
    path.write_text(
        "_time,host,source,sourcetype,_raw\n"
        "1758794400.123,E1,/v/log,st,epoch float\n"
        "2026-09-25T10:01:00Z,E2,/v/log,st,iso zulu\n"
        "2026-09-25 10:02:00,E3,/v/log,st,splunk default\n"
        "not-a-date,E4,/v/log,st,garbage timestamp\n"
        ",E5,/v/log,st,empty timestamp\n",
        encoding="utf-8",
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 5, stats
    for event in _stored(factory):
        assert event.timestamp is not None, "every row must carry a timestamp"


def test_fuzz_csv_ragged_and_extra_columns(tmp_path, factory):
    """Rows with missing and unexpected columns (schema drift mid-export)."""
    path = tmp_path / "ragged.csv"
    path.write_text(
        "_time,host,source,sourcetype,_raw,extra_field,another\n"
        "2026-09-25T10:00:00,H1,/v/log,st,row one,x,y\n"
        "2026-09-25T10:01:00,H2,/v/log,st,row two\n"
        "2026-09-25T10:02:00,H3\n"
        ",H4,/v/log,st,row four no time\n",
        encoding="utf-8",
    )
    stats = sb.ingest_csv_events(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 4, stats


def test_fuzz_csv_empty_headerless_header_only(tmp_path, factory):
    """Degenerate files: empty, headerless junk, header-only."""
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    stats = sb.ingest_csv_events(empty, session_factory=factory)
    _assert_safe_stats(stats)
    assert not stats["success"] and stats.get("error"), stats

    headerless = tmp_path / "headerless.csv"
    headerless.write_text("2026-09-25T10:00:00,H1,/v/log,st,raw\n", encoding="utf-8")
    stats = sb.ingest_csv_events(headerless, session_factory=factory)
    _assert_safe_stats(stats)  # first junk row becomes the header; 0 data rows
    assert stats["rows_read"] == 0, stats

    header_only = tmp_path / "header_only.csv"
    header_only.write_text("_time,host,source,sourcetype,_raw\n", encoding="utf-8")
    stats = sb.ingest_csv_events(header_only, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 0, stats


def test_fuzz_csv_missing_file_is_clean_error(tmp_path, factory):
    stats = sb.ingest_csv_events(tmp_path / "ghost.csv", session_factory=factory)
    _assert_safe_stats(stats)
    assert not stats["success"] and "not found" in stats.get("error", ""), stats


# ---------------------------------------------------------------------------
# JSON notables variance
# ---------------------------------------------------------------------------


def _write_json(path, content_bytes):
    path.write_bytes(content_bytes)
    return path


def test_fuzz_json_truncated_array(tmp_path, factory):
    """Export cut off mid-object (crashed exporter, partial download)."""
    good = json.dumps({
        "sourcetype": "splunk:notable", "source": "SOC-FUZZ (synthetic)",
        "host": "h1", "_time": "2026-09-25T10:00:00Z", "rule_name": "SMOKE r1",
    })
    truncated = ("[" + good + ",{").encode("utf-8")
    path = _write_json(tmp_path / "truncated.json", truncated)
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert not stats["success"] and "invalid JSON" in stats.get("error", ""), stats
    assert _stored(factory) == [], "nothing may ingest from a truncated file"


def test_fuzz_json_non_utf8_bytes(tmp_path, factory):
    """Latin-1 encoded export: must be a clean error, never a raised crash."""
    payload = '[{"sourcetype":"st","source":"SOC-FUZZ","host":"h1","_raw":"caf\xe9"}]'
    path = _write_json(tmp_path / "latin1.json", payload.encode("latin-1"))
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert not stats["success"] and stats.get("error"), stats


def test_fuzz_json_extreme_and_garbage_timestamps(tmp_path, factory):
    """The timestamp decision table under the 2026-10-05 semantic change:
    corrupt claims (1e20 overflow, "garbage") SKIP with error entries;
    valid epochs (0, negatives) parse; a MISSING claim (empty string)
    keeps the arrival-time fallback."""
    objects = [
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1", "_time": 1e20,
         "rule_name": "SMOKE overflow"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h2", "_time": 0,
         "rule_name": "SMOKE epoch zero"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h3", "_time": -5000,
         "rule_name": "SMOKE negative"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h4", "_time": "",
         "rule_name": "SMOKE empty"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h5", "_time": "garbage",
         "rule_name": "SMOKE garbage"},
    ]
    path = _write_json(
        tmp_path / "timestamps.json",
        json.dumps(objects).encode("utf-8"),
    )
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 3, stats
    assert stats["rows_skipped"] == 2, stats
    assert len(stats["errors"]) == 2 and all(
        "corrupt _time" in e for e in stats["errors"]
    ), stats
    stored_hosts = {e.host for e in _stored(factory)}
    assert stored_hosts == {"h2", "h3", "h4"}, stored_hosts
    for event in _stored(factory):
        assert event.timestamp is not None


def test_fuzz_json_wrong_shapes(tmp_path, factory):
    """Top-level string, array of strings, nested object — all clean errors."""
    for name, body in (
        ("string.json", b'"just a string"'),
        ("array_of_strings.json", b'["a", "b"]'),
        ("nested.json", b'{"a": {"b": 1}}'),
        ("array_of_nulls.json", b"[null, null]"),
    ):
        stats = sb.ingest_json_notables(tmp_path / name, session_factory=factory)
        _assert_safe_stats(stats)
        assert not stats["success"] and stats.get("error"), (name, stats)


def test_fuzz_json_mixed_valid_and_minimal_objects(tmp_path, factory):
    """A valid notable beside a keyless object (defaults must apply)."""
    objects = [
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1",
         "_time": "2026-09-25T10:00:00Z", "rule_name": "SMOKE real"},
        {},
    ]
    path = _write_json(tmp_path / "mixed.json", json.dumps(objects).encode("utf-8"))
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 2, stats


def test_fuzz_csv_in_file_duplicates_all_inserted_before_fix():
    """FINDING (fixed 2026-10-05): with autoflush off, same-file duplicate
    rows were invisible to the dedup query and ALL inserted. Pinned: the
    engine must skip in-file duplicates, not just cross-run ones."""
    factory2 = _fresh_factory()
    path = _tmp_path_factory() / "in_file_dupes.csv"
    path.write_text(
        "_time,host,source,sourcetype,_raw\n"
        "2026-09-25T10:00:00,H1,/v/log,st,alpha\n"
        "2026-09-25T10:00:00,H1,/v/log,st,alpha\n",
        encoding="utf-8",
    )
    stats = sb.ingest_csv_events(path, session_factory=factory2)
    _assert_safe_stats(stats)
    assert stats["rows_inserted"] == 1 and stats["rows_skipped"] == 1, stats


def test_fuzz_json_same_second_identical_payload_dedup_in_run():
    """FINDING (fixed 2026-10-05): the JSON engine's raw-hash dedup only
    compared committed rows, so same-second identical payloads inside ONE
    file all inserted. Pinned: exactly one survives."""
    factory2 = _fresh_factory()
    objects = [
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1",
         "_time": 1758794400, "rule_name": "SMOKE twin"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1",
         "_time": 1758794400, "rule_name": "SMOKE twin"},
    ]
    path = _tmp_path_factory() / "twins.json"
    path.write_text(json.dumps(objects), encoding="utf-8")
    stats = sb.ingest_json_notables(path, session_factory=factory2)
    _assert_safe_stats(stats)
    assert stats["rows_inserted"] == 1 and stats["rows_skipped"] == 1, stats


def test_fuzz_json_extreme_epoch_falls_back_not_crash():
    """FINDING (fixed 2026-10-05): epoch 1e20 raised OverflowError inside
    fromtimestamp, crashing the whole run. Now doubly pinned: no crash —
    AND (post semantic change) the corrupt claim SKIPS with an error
    entry instead of falling back to now."""
    factory2 = _fresh_factory()
    objects = [
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1", "_time": 1e20},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h2",
         "_time": "2026-09-25T10:00:00Z"},
    ]
    path = _tmp_path_factory() / "overflow.json"
    path.write_text(json.dumps(objects), encoding="utf-8")
    stats = sb.ingest_json_notables(path, session_factory=factory2)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 1, stats
    assert stats["rows_skipped"] == 1 and stats["errors"], stats
    assert any("corrupt _time" in e for e in stats["errors"]), stats


def test_fuzz_json_missing_timestamp_still_falls_back_to_now():
    """The semantic line: a MISSING claim (key absent, null, empty string)
    is not corrupt — the row keeps the arrival-time fallback. Only a
    PRESENT-but-unparseable claim skips."""
    factory2 = _fresh_factory()
    objects = [
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "absent"},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "null_ts", "_time": None},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "empty_ts", "_time": "   "},
        {"sourcetype": "st", "source": "SOC-FUZZ", "host": "good",
         "_time": "2026-09-25T10:00:00Z"},
    ]
    path = _tmp_path_factory() / "missing.json"
    path.write_text(json.dumps(objects), encoding="utf-8")
    stats = sb.ingest_json_notables(path, session_factory=factory2)
    _assert_safe_stats(stats)
    assert stats["success"] and stats["rows_inserted"] == 4, stats
    assert stats["rows_skipped"] == 0, stats
    for event in _stored(factory2):
        assert event.timestamp is not None


def test_fuzz_json_dedup_same_second_hash(tmp_path, factory):
    """Same-second events: identical payload dedups, distinct payload survives."""
    base = {"sourcetype": "st", "source": "SOC-FUZZ", "host": "h1",
            "_time": 1758794400}
    objects = [dict(base, rule_name="SMOKE dup"), dict(base, rule_name="SMOKE dup"),
               dict(base, rule_name="SMOKE other")]
    path = _write_json(
        tmp_path / "dedup.json", json.dumps(objects).encode("utf-8")
    )
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["rows_inserted"] == 2 and stats["rows_skipped"] == 1, stats

    again = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(again)
    assert again["rows_inserted"] == 0, again


def test_fuzz_json_raw_truncation_cap(tmp_path, factory):
    """A huge field must be stored bounded at 2000 chars, never rejected."""
    objects = [{
        "sourcetype": "st", "source": "SOC-FUZZ", "host": "h1",
        "_time": "2026-09-25T10:00:00Z", "rule_name": "SMOKE big",
        "description": "x" * 5000,
    }]
    path = _write_json(tmp_path / "big.json", json.dumps(objects).encode("utf-8"))
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert stats["rows_inserted"] == 1, stats
    stored = _stored(factory)[0]
    assert len(stored.raw or "") <= 2000
    assert "SMOKE big" in stored.raw


def test_fuzz_json_event_cap_enforced(tmp_path, factory, monkeypatch):
    """Over-cap batches fail closed with a clear error (cap tightened for test)."""
    monkeypatch.setenv("SPLUNK_BOUNDARY_MAX_EVENTS", "5")
    objects = [{"sourcetype": "st", "source": "SOC-FUZZ", "host": f"h{i}",
                "_time": "2026-09-25T10:00:00Z"} for i in range(7)]
    path = _write_json(
        tmp_path / "over.json", json.dumps(objects).encode("utf-8")
    )
    stats = sb.ingest_json_notables(path, session_factory=factory)
    _assert_safe_stats(stats)
    assert not stats["success"] and "SPLUNK_BOUNDARY_MAX_EVENTS" in stats.get("error", ""), stats
    assert _stored(factory) == [], "over-cap batch must insert nothing"


def _fresh_factory():
    """Standalone isolated factory for the finding-pinning tests."""
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
    return sessionmaker(bind=engine, autoflush=False)


def _tmp_path_factory():
    import tempfile

    return Path(tempfile.mkdtemp(prefix="fuzz-findings-"))


# ---------------------------------------------------------------------------
# The full gate: validate_file on hostile inputs (fail-closed front door)
# ---------------------------------------------------------------------------


def test_fuzz_validate_rejects_binary_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("SPLUNK_BOUNDARY_MAX_BYTES", "64")
    nul_csv = tmp_path / "nul.csv"
    nul_csv.write_bytes(b"_time,host,_raw\n2026-09-25T10:00:00,H1,a\x00b\n")
    report = sb.validate_file(nul_csv)
    assert report["ok"] is False and report["violations"], report

    oversize = tmp_path / "big.csv"
    oversize.write_bytes(b"_time,host,_raw\n" + b"x" * 100)
    report = sb.validate_file(oversize)
    assert report["ok"] is False, report

    control_heavy = tmp_path / "controls.log"
    control_heavy.write_bytes(b"\x01\x02\x03\x04\x05" * 20)
    report = sb.validate_file(control_heavy)
    assert report["ok"] is False, report


def test_fuzz_full_admit_of_messy_csv_round_trip(tmp_path, factory, monkeypatch):
    """End-to-end hermetic boundary lifecycle on a messy-but-legal CSV:
    validate -> quarantine -> ingest (into the isolated DB) -> manifest
    carries exact ids -> purge targets exactly those ids. The platform DB
    is never touched: ingest is wrapped to the sqlite factory and the
    purge's DB deletion is recorded via the same seam the existing suite
    mocks (_delete_events_by_ids)."""
    deleted: list = []
    monkeypatch.setattr(
        sb, "_delete_events_by_ids", lambda ids: deleted.extend(ids) or len(ids)
    )

    real_ingest = sb.ingest_csv_events

    def isolated_ingest(path, silent=True, session_factory=None):
        return real_ingest(path, silent=silent, session_factory=factory)

    monkeypatch.setattr(sb, "ingest_csv_events", isolated_ingest)

    messy = tmp_path / "messy.csv"
    messy.write_text(
        '_time,host,source,sourcetype,_raw\n'
        '2026-09-25T10:00:00,"host, w",/v/log,st,"multi\nline"\n'
        '2026-09-25T10:00:00,"host, w",/v/log,st,"multi\nline"\n'
        "2026-09-25T10:01:00,H2,/v/log,st,plain\n"
        "garbage-timestamp,H9,/v/log,st,fallback-now\n",
        encoding="utf-8",
    )

    report = sb.validate_file(messy)
    assert report["ok"] is True, report
    manifest = sb.quarantine_file(messy, source_label="fuzz:admit-roundtrip")

    stats = sb.ingest_manifest(manifest)
    _assert_safe_stats(stats)
    assert stats["rows_inserted"] == 3 and stats["rows_skipped"] == 1, stats
    assert stats["inserted_ids"], "manifest must record exact ids for the purge"
    assert len(_stored(factory)) == 3

    # ...and the purge targets exactly what the manifest recorded.
    purge = sb.purge_batch(manifest["batch_id"])
    assert purge["purge_strategy"] == "ids", purge
    assert deleted == sorted(stats["inserted_ids"]), (deleted, stats["inserted_ids"])
    assert purge["staged_removed"] is True, purge
    assert purge["events_deleted"] == 3, purge


# ===========================================================================
# Paste-box path (admit_text -> paste_notable -> record_paste_ingest)
# ===========================================================================


class TestPasteBoxIngestFuzz:
    """Adversarial variance against the analyst paste path, end to end.

    The real paste_notable handler runs against an isolated sqlite DB
    (same seam as the C4 suite: db_models.SessionLocal) and an isolated
    staging dir (autouse fixture above). Contract: malformed input either
    produces a structured response or a clean HTTPException — never a
    raised surprise — and every admitted batch is purgeable.
    """

    @pytest.fixture()
    def paste_env(self, monkeypatch, tmp_path):
        import db.models as db_models
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        monkeypatch.setattr(db_models, "SessionLocal", None)  # guard vs real DB
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db_models.Base.metadata.create_all(bind=engine)
        test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(db_models, "SessionLocal", test_session)
        yield test_session
        engine.dispose()

    @staticmethod
    def _paste(text):
        from api.routes import notables as notables_route
        from api.schemas import PastedNotableRequest

        return notables_route.paste_notable(PastedNotableRequest(raw_text=text))

    @staticmethod
    def _paste_bytes(n):
        """A legal-looking paste padded to exactly n UTF-8 bytes."""
        prefix = "Notable\nTitle: SMOKE byte budget\nDescription: "
        base = prefix.encode("utf-8")
        assert base and len(base) < n
        filler = n - len(base) - 1  # leave room for the trailing newline
        return prefix + "a" * filler + "\n"

    def test_fuzz_paste_literal_newline_flattening(self, paste_env):
        """Splunk table-cell copies deliver literal \\n sequences — the
        normalizer must reconstruct line structure so parsing still works."""
        flat = (
            "Notable\\nTitle: SMOKE flattened\\nRule ID: esca_rule@@notable@@f1at\\n"
            "Host: FLAT01\\nUser: smoke_user\\n"
            "Description: literal-newline paste survives normalization\\n"
        )
        resp = self._paste(flat)
        assert resp["success"] is True and resp["added"] == 1, resp
        assert resp["batch_id"], resp

    def test_fuzz_paste_multibyte_byte_budget(self, paste_env, monkeypatch):
        """The cap is a BYTE budget: a multibyte paste under it is accepted,
        one over it is a clean 413 — and rejection happens BEFORE admission
        (no staging litter)."""
        from api.routes import notables as notables_route
        from fastapi import HTTPException

        monkeypatch.setattr(notables_route, "PASTE_MAX_BYTES", 1000)

        resp = self._paste(self._paste_bytes(900))
        assert resp["success"] is True and resp["added"] == 1, resp

        with pytest.raises(HTTPException) as excinfo:
            self._paste(self._paste_bytes(1001))
        assert excinfo.value.status_code == 413, excinfo.value

        import db.models as db_models
        assert paste_env().query(db_models.SplunkEvent).count() == 1, (
            "only the under-budget paste may have inserted"
        )

    def test_fuzz_paste_empty_and_whitespace_only(self, paste_env):
        """Empty and whitespace-only pastes are clean 400s, no batches."""
        from fastapi import HTTPException

        for text in ("", "   ", "\n\n\t "):
            with pytest.raises(HTTPException) as excinfo:
                self._paste(text)
            assert excinfo.value.status_code == 400, (text, excinfo.value)

        assert _stored(paste_env) == []

    def test_fuzz_paste_bom_control_chars_and_zero_widths(self, paste_env):
        """BOM, vertical tab/form feed, and zero-width joiners must not
        crash the handler; structured response either way."""
        hostile = (
            "\ufeffNotable\nTitle: SMOKE control\x0bchars\x0c and \u200bzero\u200dwidths\n"
            "Host: CTRL01\nUser: smoke_user\n"
            "Description: control-character paste\n"
        )
        resp = self._paste(hostile)
        assert isinstance(resp, dict) and "success" in resp, resp

    def test_fuzz_paste_failure_after_admit_auto_purges(self, paste_env, monkeypatch):
        """If the pipeline fails AFTER admission (here: segment detection
        finds nothing), the handler must purge the batch it admitted —
        no staging litter from failed pastes."""
        from api.routes import notables as notables_route
        from fastapi import HTTPException
        from services import splunk_boundary as sb

        monkeypatch.setattr(notables_route, "split_pasted_notables", lambda _: [])
        with pytest.raises(HTTPException) as excinfo:
            self._paste("Notable\nTitle: SMOKE doomed\nHost: H1\n")
        assert excinfo.value.status_code == 400, excinfo.value

        assert sb.list_batches() == [], "failed paste must leave no batch"
        assert _stored(paste_env) == []

    def test_fuzz_paste_duplicate_repaste_dedups(self, paste_env):
        """The identical hostile-ish paste twice: second is a skip, and
        both pastes still own distinct purgeable batches."""
        text = "Notable\nTitle: SMOKE dedup\nRule ID: esca_rule@@notable@@dedup12345678\nHost: D01\n"
        first = self._paste(text)
        assert first["added"] == 1, first
        second = self._paste(text)
        assert second["added"] == 0 and second["skipped"] == 1, second
        assert first["batch_id"] != second["batch_id"]
        from services import splunk_boundary as sb
        assert len(sb.list_batches()) == 2, "each paste is its own batch"


# ===========================================================================
# splunk_csv_ingestor tool (CLI wrapper around the boundary)
# ===========================================================================


class TestCsvIngestorToolFuzz:
    """The CLI tool is admit_file + operator output; fuzz it through the
    same matrix. Its DB leg resolves via db.models.SessionLocal, so the
    sqlite patch makes ingest AND purge hermetic. Contract: the tool
    returns a stats dict for every input — success or a clean error —
    and never raises."""

    @pytest.fixture(autouse=True)
    def tool_env(self, monkeypatch):
        import db.models as db_models
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        monkeypatch.setattr(db_models, "SessionLocal", None)  # guard vs real DB
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        db_models.Base.metadata.create_all(bind=engine)
        self.factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        monkeypatch.setattr(db_models, "SessionLocal", self.factory)
        yield
        engine.dispose()

    @classmethod
    def _tool(cls):
        import importlib.util

        if not hasattr(cls, "_module"):
            tool_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), os.pardir,
                             "Tools", "splunk_csv_ingestor", "splunk_csv_ingestor.py")
            )
            spec = importlib.util.spec_from_file_location("splunk_csv_ingestor_fuzz", tool_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls._module = module
        return cls._module

    def test_fuzz_tool_clean_csv_round_trip_and_purge(self, tmp_path):
        """Legal CSV through the tool, then purge the batch the tool's
        stats point at — rows must vanish from the isolated DB."""
        from services import splunk_boundary as sb

        path = tmp_path / "clean.csv"
        path.write_text(
            "_time,host,source,sourcetype,_raw\n"
            "2026-09-25T10:00:00,H1,/v/log,st,one\n"
            "2026-09-25T10:01:00,H2,/v/log,st,two\n",
            encoding="utf-8",
        )
        stats = self._tool().ingest_splunk_csv(str(path), silent=True)
        assert stats["success"] is True and stats["rows_inserted"] == 2, stats
        assert stats["batch_id"], stats
        assert len(_stored(self.factory)) == 2

        purge = sb.purge_batch(stats["batch_id"])
        assert purge["events_deleted"] == 2 and purge["purge_strategy"] == "ids", purge
        assert _stored(self.factory) == []

    def test_fuzz_tool_latin1_and_multiline_and_dupes(self, tmp_path):
        """Engine-tolerated variance stays tolerated through the tool."""
        latin = tmp_path / "latin.csv"
        latin.write_bytes(
            b"_time,host,source,sourcetype,_raw\n"
            b"2026-09-25T10:00:00,H1,/v/log,st,caf\xe9 row\n"
        )
        stats = self._tool().ingest_splunk_csv(str(latin), silent=True)
        assert stats["success"] is True and stats["rows_inserted"] == 1, stats

        messy = tmp_path / "messy.csv"
        # Distinct keys from the latin-1 row above — same keys would be
        # legitimately skipped as cross-file duplicates (the dedup working).
        messy.write_text(
            '_time,host,source,sourcetype,_raw\n'
            '2026-09-25T11:00:00,H5,/v/log,st,"multi\nline"\n'
            '2026-09-25T11:00:00,H5,/v/log,st,"multi\nline"\n',
            encoding="utf-8",
        )
        stats = self._tool().ingest_splunk_csv(str(messy), silent=True)
        _assert_safe_stats(stats)
        assert stats["rows_inserted"] == 1 and stats["rows_skipped"] == 1, (
            "in-file duplicate must be skipped, not double-inserted",
            stats,
        )

    def test_fuzz_tool_rejects_binary_and_missing_files(self, tmp_path):
        """Boundary-refused inputs surface as clean failure dicts."""
        nul = tmp_path / "nul.csv"
        nul.write_bytes(b"_time,host,_raw\n2026-09-25T10:00:00,H1,a\x00b\n")
        stats = self._tool().ingest_splunk_csv(str(nul), silent=True)
        assert stats["success"] is False and stats.get("error"), stats

        missing = self._tool().ingest_splunk_csv(str(tmp_path / "ghost.csv"), silent=True)
        assert missing["success"] is False and missing.get("error"), missing
        # (The tool has a FileNotFoundError branch, but the boundary turns a
        # missing file into a validation ValueError first — the dict comes
        # out the ValueError branch; either way the contract holds.)

    def test_fuzz_tool_empty_csv_and_json_passthrough(self, tmp_path):
        """Empty CSV fails inside ingest (post-quarantine); a .json file is
        routed to the JSON engine by extension — a tool quirk worth pinning."""
        empty = tmp_path / "empty.csv"
        empty.write_text("", encoding="utf-8")
        stats = self._tool().ingest_splunk_csv(str(empty), silent=True)
        assert stats["success"] is False and stats.get("error"), stats
        assert _stored(self.factory) == [], "failed ingest must insert nothing"

        note = tmp_path / "notes.json"
        note.write_text(json.dumps([{
            "sourcetype": "st", "source": "SOC-FUZZ (tool)", "host": "h1",
            "_time": "2026-09-25T10:00:00Z", "rule_name": "SMOKE via tool",
        }]), encoding="utf-8")
        stats = self._tool().ingest_splunk_csv(str(note), silent=True)
        assert stats["success"] is True and stats["rows_inserted"] == 1, stats

    def test_fuzz_tool_never_raises_on_hostile_inputs(self, tmp_path):
        """Blanket sweep: every hostile file yields a dict, never an raise."""
        hostile_files = {
            "controls.log": b"\x01\x02\x03" * 40,
            "no_header.csv": b"2026-09-25T10:00:00,H1,raw\n",
            "half_row.csv": b"_time,host,_raw\n2026-09-25T10:00:00\n",
            "truncated.json": b'[{"host": "h1", ',
            "binary.exe": b"MZ\x90\x00\x03",
        }
        for name, body in hostile_files.items():
            p = tmp_path / name
            p.write_bytes(body)
            stats = self._tool().ingest_splunk_csv(str(p), silent=True)
            assert isinstance(stats, dict), (name, stats)
            assert "success" in stats, (name, stats)
