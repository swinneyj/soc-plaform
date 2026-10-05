"""Property-based fuzz layer for the Splunk boundary ingest engines.

Where tests/test_boundary_ingest_fuzz.py drives FIXED adversarial cases,
this layer uses Hypothesis to generate randomized malformed CSV/JSON and
asserts SAFETY ORACLES that must hold for every input:

  Contract oracle   stats are self-consistent (read == inserted + skipped;
                    inserted_ids match; errors exist iff rows were skipped
                    for causes other than dedup);
  Key oracle        inserted == distinct dedup keys (both engines dedup on
                    (sourcetype, source, host, timestamp[, raw-hash]); the
                    oracle re-derives that set independently, with missing
                    _time treated as distinct arrival-fallback keys);
  Corrupt oracle    rows whose _time is present-but-unparseable are never
                    inserted and always produce an error entry (the
                    2026-10-05 semantic: corrupt claims skip visibly);
  Storage oracle    every stored row is bounded (raw <= 2000 chars) and
                    carries a non-null timestamp;
  Never-raise       any malformed byte/text input returns a stats dict.

Run profile: 50 examples/deadline-off by default (CI-friendly); tune with
SOC_FUZZ_EXAMPLES. Hypothesis prints concrete falsifying examples on
failure — replay one locally with @example(...) or the printed values.

Hermetic by construction: sqlite factories and temp dirs are created
per example; no platform DB or real staging dir is touched.
"""

import csv as _csv
import datetime
import hashlib
import io
import json
import os
import tempfile

import pytest
from hypothesis import given, settings, strategies as st

from services import splunk_boundary as sb

# ---------------------------------------------------------------------------
# Hypothesis profile (CI-friendly defaults; SOC_FUZZ_EXAMPLES to tune)
# ---------------------------------------------------------------------------

settings.register_profile(
    "soc_fuzz",
    max_examples=int(os.environ.get("SOC_FUZZ_EXAMPLES", "50")),
    deadline=None,
)
settings.load_profile("soc_fuzz")


# ---------------------------------------------------------------------------
# Strategies: randomized malformed inputs
# ---------------------------------------------------------------------------

VALID_TS = ["2026-09-25T10:00:00", "2026-09-25 10:00:00", "2026-09-25T10:00:00Z"]
CORRUPT_TS_TEXT = ["garbage", "not-a-date", "2026-13-45", "0xZZ", "null-ish"]

json_time_values = st.one_of(
    st.none(),
    st.booleans(),                       # wrong type (bool is an int!)
    st.integers(min_value=-10**6, max_value=10**11),   # in/out of range epochs
    st.floats(allow_nan=True, allow_infinity=True, width=32),
    st.sampled_from(VALID_TS),
    st.sampled_from(CORRUPT_TS_TEXT),
    st.text(max_size=24),                # arbitrary garbage strings
)

csv_time_values = st.one_of(
    st.none(),
    st.sampled_from(VALID_TS + CORRUPT_TS_TEXT),
    st.text(max_size=24),
)

field_text = st.one_of(
    st.none(),
    st.text(max_size=40),                # hosts/sources/raw incl. newlines, NULs, unicode
)

CSV_KEY_POOL = ["sourcetype", "source::type", "source", "_source",
                "host", "_host", "_raw", "raw", "_time", "time", "timestamp",
                "extra_a", "extra_b"]   # unique after .lower()


@st.composite
def csv_row_dicts(draw):
    """A batch of CSV rows over the Splunk export column pool."""
    n = draw(st.integers(min_value=0, max_value=6))
    rows = []
    for _ in range(n):
        names = draw(st.sets(st.sampled_from(CSV_KEY_POOL), min_size=1, max_size=8))
        row = {name: draw(field_text) for name in sorted(names)}
        rows.append(row)
    return rows


@st.composite
def json_notable_objects(draw):
    """A batch of notable objects with randomized field presence and types."""
    n = draw(st.integers(min_value=0, max_value=6))
    objects = []
    for _ in range(n):
        obj = {}
        if draw(st.booleans()):
            obj["sourcetype"] = draw(field_text)
        if draw(st.booleans()):
            obj["source"] = draw(field_text)
        if draw(st.booleans()):
            obj["host"] = draw(field_text)
        if draw(st.booleans()):
            obj["_time"] = draw(json_time_values)
        if draw(st.booleans()):
            obj["timestamp"] = draw(json_time_values)
        if draw(st.booleans()):
            obj["rule_name"] = draw(st.text(max_size=30))
        if draw(st.booleans()):
            obj["description"] = draw(st.text(max_size=2200))  # pushes the 2000 cap
        objects.append(obj)
    return objects


# ---------------------------------------------------------------------------
# Hermetic helpers (per example — hypothesis forbids scoped fixtures here)
# ---------------------------------------------------------------------------


def _fresh_factory():
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


def _stored_rows(factory):
    from db.models import SplunkEvent

    session = factory()
    try:
        return (
            session.query(SplunkEvent)
            .order_by(SplunkEvent.id)
            .all()
        )
    finally:
        session.close()


def _assert_contract(stats):
    """The stats contract every ingest result must obey."""
    assert isinstance(stats, dict)
    for key in ("rows_read", "rows_inserted", "rows_skipped"):
        assert key in stats, stats
    assert stats["rows_read"] == stats["rows_inserted"] + stats["rows_skipped"], stats
    assert stats["rows_inserted"] == len(stats.get("inserted_ids") or []), stats
    if not stats.get("error"):
        assert stats.get("success") is True, stats


def _cell(value):
    return "" if value is None else str(value)


def _parse_ts_like_engine(text):
    """Replicate the engines' ISO parsing on a string value (per-runtime
    exact: the oracle and the engine share the interpreter's fromisoformat).
    Returns naive datetime or None (unparseable)."""
    try:
        ts = datetime.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        return ts
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# JSON oracle
# ---------------------------------------------------------------------------


def _json_time_outcome(value):
    """(status, ts) for a generated _time value under engine semantics:
    status in {'missing', 'valid', 'corrupt'}."""
    if value is None:
        return "missing", None
    if isinstance(value, bool):
        return "corrupt", None
    if isinstance(value, (int, float)):
        try:
            return "valid", datetime.datetime.fromtimestamp(
                float(value), tz=datetime.timezone.utc
            ).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return "corrupt", None
    if isinstance(value, str):
        if not value.strip():
            return "missing", None
        ts = _parse_ts_like_engine(value)
        return ("valid", ts) if ts is not None else ("corrupt", None)
    return "corrupt", None


def _json_oracle(objects):
    """Independent re-derivation of what the JSON engine must do.
    Returns (must_insert, corrupt_count)."""
    must_insert_keys = set()
    corrupt = 0
    fallback_seq = 0
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        status, ts = _json_time_outcome(obj.get("_time", obj.get("timestamp")))
        if status == "corrupt":
            corrupt += 1
            continue
        if status == "missing":
            # Arrival-time fallback: distinct per row (distinct "now"s).
            fallback_seq += 1
            key_ts = ("FALLBACK", fallback_seq)
        else:
            key_ts = ts
        raw_id = json.dumps(obj, ensure_ascii=False)  # mirrors the engine exactly
        must_insert_keys.add((
            str(obj.get("sourcetype") or "splunk:notable"),
            str(obj.get("source") or "splunk_json_export"),
            str(obj.get("host") or "unknown"),
            key_ts,
            raw_id,
        ))
    return len(must_insert_keys), corrupt


# ---------------------------------------------------------------------------
# JSON properties
# ---------------------------------------------------------------------------


@given(objects=json_notable_objects())
def test_property_json_contract_key_and_corrupt_oracles(objects):
    factory = _fresh_factory()
    with tempfile.TemporaryDirectory(prefix="prop-json-") as tmp:
        path = os.path.join(tmp, "notables.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(objects, fh, ensure_ascii=False)

        stats = sb.ingest_json_notables(path, session_factory=factory)
        _assert_contract(stats)

        must_insert, corrupt = _json_oracle(objects)
        assert stats["rows_inserted"] == must_insert, (stats, objects)
        assert stats["rows_skipped"] >= corrupt, stats
        if corrupt:
            assert sum(
                1 for e in stats["errors"] if "corrupt _time" in e
            ) == corrupt, stats

        rows = _stored_rows(factory)
        assert len(rows) == must_insert, (len(rows), must_insert)
        for row in rows:
            assert row.raw is not None and len(row.raw) <= 2000, row
            assert row.timestamp is not None, row
        factory.kw["bind"].dispose()


@given(payload=st.one_of(st.text(max_size=4000), st.binary(max_size=4000)))
def test_property_json_never_raises_on_any_bytes(payload):
    factory = _fresh_factory()
    with tempfile.TemporaryDirectory(prefix="prop-json-raw-") as tmp:
        path = os.path.join(tmp, "hostile.json")
        mode = "w" if isinstance(payload, str) else "wb"
        with open(path, mode, encoding=None if isinstance(payload, str) else None) as fh:
            fh.write(payload)
        stats = sb.ingest_json_notables(path, session_factory=factory)
        assert isinstance(stats, dict) and "success" in stats, stats


# ---------------------------------------------------------------------------
# CSV oracle
# ---------------------------------------------------------------------------


def _csv_raises_on_nul():
    """Runtime probe: does this interpreter's csv module raise on NUL?
    Older CPython (3.9) raises csv.Error; newer (3.13+) parses NUL
    cells fine. The oracle must model the runtime, not one behavior."""
    probe = io.StringIO("a\x00b\n")
    try:
        next(_csv.reader(probe))
        return False
    except _csv.Error:
        return True


CSV_RAISES_ON_NUL = _csv_raises_on_nul()


def _csv_oracle(rows):
    """Independent re-derivation for the CSV engine. All values are strings
    after the file round trip; _time unparseable/missing -> distinct
    arrival-fallback keys (per row). On runtimes whose csv raises on NUL
    (3.9): the FIRST row containing NUL in any cell aborts parsing at that
    line (the stream cannot be resynced after a mid-record error) — that
    row and every row after it are neither read nor inserted. On runtimes
    that accept NUL (3.13+), rows ingest like any other.    Returns (must_insert, stream_skips, expected_rows_read)."""
    keys = set()
    fallback_seq = 0
    stream_skips = 0
    expected_rows_read = len(rows)
    if CSV_RAISES_ON_NUL:
        nul_idx = next(
            (i for i, row in enumerate(rows)
             if any("\x00" in _cell(v) for v in row.values())),
            None,
        )
        if nul_idx is not None:
            rows = rows[:nul_idx]
            stream_skips = 1
            expected_rows_read = nul_idx + 1  # NUL row counted, rest unread
    for row in rows:
        field_lower = {k.lower(): _cell(v) for k, v in row.items() if k}
        sourcetype = field_lower.get("sourcetype") or field_lower.get("source::type") or "splunk:notable"
        src = field_lower.get("source") or field_lower.get("_source") or "splunk_export"
        host = field_lower.get("host") or field_lower.get("_host") or "unknown"
        ts_str = field_lower.get("_time") or field_lower.get("time") or field_lower.get("timestamp")
        if not ts_str:
            fallback_seq += 1
            key_ts = ("FALLBACK", fallback_seq)
        else:
            ts = _parse_ts_like_engine(ts_str)
            key_ts = ts if ts is not None else ("FALLBACK", fallback_seq := fallback_seq + 1)
        keys.add((sourcetype, src, host, key_ts, field_lower.get("_raw") or field_lower.get("raw") or str(row)))
    return len(keys), stream_skips, expected_rows_read


# ---------------------------------------------------------------------------
# CSV properties
# ---------------------------------------------------------------------------


@given(rows=csv_row_dicts())
def test_property_csv_contract_and_key_oracle(rows):
    factory = _fresh_factory()
    with tempfile.TemporaryDirectory(prefix="prop-csv-") as tmp:
        path = os.path.join(tmp, "events.csv")
        fieldnames = sorted({name for row in rows for name in row}) or ["_time"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = _csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        stats = sb.ingest_csv_events(path, session_factory=factory)
        _assert_contract(stats)

        must_insert, stream_skips, expected_rows_read = _csv_oracle(rows)
        assert stats["rows_read"] == expected_rows_read, (stats, rows)
        assert stats["rows_inserted"] == must_insert, (stats, rows)
        assert stats["rows_skipped"] == stream_skips, stats

        for row in _stored_rows(factory):
            assert row.raw is not None and len(row.raw) <= 2000, row
            assert row.timestamp is not None, row
        factory.kw["bind"].dispose()


@given(payload=st.one_of(st.text(max_size=4000), st.binary(max_size=4000)))
def test_property_csv_never_raises_on_any_bytes(payload):
    factory = _fresh_factory()
    with tempfile.TemporaryDirectory(prefix="prop-csv-raw-") as tmp:
        path = os.path.join(tmp, "hostile.csv")
        mode = "w" if isinstance(payload, str) else "wb"
        with open(path, mode, encoding=None if isinstance(payload, str) else None) as fh:
            fh.write(payload)
        stats = sb.ingest_csv_events(path, session_factory=factory)
        assert isinstance(stats, dict) and "success" in stats, stats
