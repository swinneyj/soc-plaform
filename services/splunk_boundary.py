"""
Splunk boundary ("the latch") — the single, inspectable choke point for all
data moving between Splunk exports and the SOC Platform.

Why this exists: Splunk is the sensitive system of record. Anything entering
this platform from it — CSV exports, notable pastes, folder-watcher drops —
must pass through one place that can be audited, throttled, and, when
needed, shut off entirely, instead of data loading paths accreting across
tools over time.

Modes (env ``SPLUNK_BOUNDARY_MODE``):

- ``quarantined`` (default) — data may only enter via :func:`admit_file`
  (validate -> copy to staging -> ingest). The HTTP upload endpoints refuse
  to operate: hosts with network-reachable APIs cannot become an unplanned
  data doorway.
- ``restricted`` — same validation, plus the HTTP endpoints are allowed
  (intended for deployments behind API-key auth).
- ``open`` — validation runs in report-only mode (size/type violations are
  warnings, not refusals). For throwaway local experiments only.

Every admitted file gets a *batch id*; everything the platform stored from
that file can be listed and purged later (:func:`purge_batch`), which keeps
the platform able to un-ingest sensitive data without touching Splunk.

Validation contract (fail-closed):
- extension allowlist: ``.csv``, ``.json``, ``.txt``, ``.log``, ``.md``
- size cap: ``SPLUNK_BOUNDARY_MAX_BYTES`` (default 50 MiB)
- binary-sniff of the first 8 KiB (NUL bytes / control-char ratio)
- path-traversal-resistant naming: only the basename is ever used
"""

import json
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ALLOWED_EXTENSIONS = {".csv", ".json", ".txt", ".log", ".md"}
DEFAULT_MAX_BYTES = 50 * 1024 * 1024
_STAMP_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")
_BINARY_SNIFF_BYTES = 8192

_MODES = ("quarantined", "restricted", "open")


def _utcnow() -> datetime:
    """Naive UTC now (matches the platform's naive-UTC convention, without
    the deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _mode() -> str:
    value = (os.environ.get("SPLUNK_BOUNDARY_MODE") or "quarantined").strip().lower()
    if value not in _MODES:
        raise ValueError(
            f"Invalid SPLUNK_BOUNDARY_MODE '{value}' (expected one of {_MODES})"
        )
    return value


def current_mode() -> str:
    """Public accessor for the configured boundary mode."""
    return _mode()


def staging_dir() -> Path:
    """Quarantine directory (created on demand), env-overridable.

    Defaults to ``<repo>/Data/quarantine``; on read-only deployments
    (e.g. Vercel serverless, where only /tmp is writable) it falls back to
    a temp-directory location so the boundary endpoints never 500.
    """
    configured = os.environ.get("SPLUNK_BOUNDARY_STAGING")
    if configured:
        root = Path(configured)
    else:
        candidate = Path(__file__).resolve().parent.parent / "Data" / "quarantine"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.touch()
            probe.unlink()
            root = candidate
        except OSError:
            root = Path(tempfile.gettempdir()) / "splunk-quarantine"
    root.mkdir(parents=True, exist_ok=True)
    return root


def max_bytes() -> int:
    raw = os.environ.get("SPLUNK_BOUNDARY_MAX_BYTES")
    return int(raw) if raw else DEFAULT_MAX_BYTES


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _looks_binary(head: bytes) -> bool:
    if b"\x00" in head:
        return True
    if not head:
        return False
    control = sum(1 for b in head if b < 9 or (13 < b < 32))
    return control / len(head) > 0.10


def validate_file(path: Union[str, Path]) -> Dict[str, Any]:
    """Fail-closed validation of an incoming file. Returns a report dict."""
    source = Path(path)
    report: Dict[str, Any] = {
        "original_path": str(source),
        "filename": source.name,  # basename only — never trust directories
        "violations": [],
        "ok": True,
    }

    if not source.is_file():
        report["ok"] = False
        report["violations"].append(f"not a regular file: {source}")
        return report

    ext = source.suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        report["ok"] = False
        report["violations"].append(
            f"extension '{ext or '(none)'}' not in allowlist {sorted(ALLOWED_EXTENSIONS)}"
        )

    size = source.stat().st_size
    report["size_bytes"] = size
    cap = max_bytes()
    if size > cap:
        report["ok"] = False
        report["violations"].append(f"size {size} exceeds cap {cap}")

    try:
        with open(source, "rb") as fh:
            head = fh.read(_BINARY_SNIFF_BYTES)
    except OSError as exc:
        report["ok"] = False
        report["violations"].append(f"unreadable: {exc}")
        return report

    if _looks_binary(head):
        report["ok"] = False
        report["violations"].append("content looks binary (NUL/control bytes)")

    return report


# ---------------------------------------------------------------------------
# Quarantine / admit / purge
# ---------------------------------------------------------------------------


def _new_batch_id() -> str:
    stamp = _utcnow().strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def quarantine_file(path: Union[str, Path], source_label: str = "") -> Dict[str, Any]:
    """Validate, then copy the file into staging under a batch id.

    Copies (never moves) so the original drop location stays intact for the
    operator. Returns the manifest describing the batch.
    """
    report = validate_file(path)
    if not report["ok"] and _mode() != "open":
        raise ValueError("boundary validation failed: " + "; ".join(report["violations"]))

    batch_id = _new_batch_id()
    staged_name = f"{batch_id}-{report['filename']}"
    staged_path = staging_dir() / staged_name
    shutil.copy2(report["original_path"], staged_path)

    manifest = {
        "batch_id": batch_id,
        "mode": _mode(),
        "source_label": source_label,
        "staged_path": str(staged_path),
        "staged_at": _utcnow().isoformat(),
        "validation": report,
    }
    (staging_dir() / f"{batch_id}-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def _load_db_module():
    from db.models import SplunkEvent, SessionLocal  # imported lazily for hermetic tests

    return SplunkEvent, SessionLocal


def ingest_csv_events(
    path: Union[str, Path],
    silent: bool = True,
    session_factory=None,
) -> Dict[str, Any]:
    """Canonical Splunk CSV -> SplunkEvent loader used by every ingest path.

    Maps common Splunk export columns (case-insensitive: sourcetype/source::
    type, source/_source, host/_host, _raw/raw, _time/time/timestamp),
    deduplicates on (sourcetype, source, host, timestamp), and truncates raw
    to 2000 chars. Timestamps normalize to naive UTC.

    ``session_factory`` lets tests inject an isolated database; production
    callers use the platform SessionLocal.
    """
    import csv as _csv

    source = Path(path)
    stats: Dict[str, Any] = {
        "success": False,
        "rows_read": 0,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "errors": [],
        "file": source.name,
    }
    if not source.is_file():
        stats["error"] = f"CSV file not found: {source}"
        return stats

    SplunkEvent, SessionLocal = _load_db_module()
    session = (session_factory or SessionLocal)()
    inserted: list = []
    try:
        with open(source, "r", encoding="utf-8", errors="ignore") as fh:
            reader = _csv.DictReader(fh)
            if not reader.fieldnames:
                stats["error"] = "CSV file is empty or malformed"
                return stats

            stats["success"] = True
            for row_num, row in enumerate(reader, start=2):
                stats["rows_read"] += 1
                try:
                    field_lower = {k.lower(): v for k, v in row.items() if k}
                    sourcetype = (
                        field_lower.get("sourcetype")
                        or field_lower.get("source::type")
                        or "splunk:notable"
                    )
                    src = field_lower.get("source") or field_lower.get("_source") or "splunk_export"
                    host = field_lower.get("host") or field_lower.get("_host") or "unknown"
                    raw = field_lower.get("_raw") or field_lower.get("raw") or str(row)

                    ts_str = (
                        field_lower.get("_time")
                        or field_lower.get("time")
                        or field_lower.get("timestamp")
                    )
                    ts = None
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
                            if ts.tzinfo is not None:
                                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                        except ValueError:
                            ts = None
                    if ts is None:
                        ts = _utcnow()

                    existing = (
                        session.query(SplunkEvent)
                        .filter(
                            SplunkEvent.sourcetype == sourcetype,
                            SplunkEvent.source == src,
                            SplunkEvent.host == host,
                            SplunkEvent.timestamp == ts,
                        )
                        .first()
                    )
                    if existing:
                        stats["rows_skipped"] += 1
                        continue

                    event = SplunkEvent(
                        sourcetype=sourcetype,
                        source=src,
                        host=host,
                        raw=raw[:2000],
                        timestamp=ts,
                    )
                    session.add(event)
                    inserted.append(event)
                    stats["rows_inserted"] += 1
                    if stats["rows_inserted"] % 50 == 0:
                        session.commit()
                except Exception as exc:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {row_num}: {str(exc)[:100]}")
            session.commit()
            stats["inserted_ids"] = [obj.id for obj in inserted]

        # Guard against silent total failure: if the database itself is
        # broken (missing tables, connection loss), every row lands in
        # errors[] while nothing was inserted. That must not read as success.
        if (
            stats["rows_read"] > 0
            and stats["rows_inserted"] == 0
            and stats["rows_skipped"] == len(stats["errors"])
        ):
            stats["success"] = False
            stats["error"] = (
                "all rows failed (likely a database error); first: "
                + (stats["errors"][0] if stats["errors"] else "unknown")
            )
        return stats
    except Exception as exc:
        session.rollback()
        stats["success"] = False
        stats["error"] = str(exc)
        return stats
    finally:
        session.close()


def ingest_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest a quarantined batch into the platform database.

    CSV batches are loaded through the existing splunk_csv_ingestor path.
    Other formats are rejected with a clear message rather than guessed at:
    JSON notables need context-aware parsing that belongs to their own
    feature, not to the boundary.
    """
    staged = Path(manifest["staged_path"])
    if not staged.is_file():
        raise FileNotFoundError(f"staged file missing: {staged}")

    t0 = _utcnow() - timedelta(seconds=1)
    if staged.name.endswith(".csv"):
        stats = ingest_csv_events(str(staged), silent=True)
    elif staged.name.endswith(".json"):
        stats = ingest_json_notables(str(staged), silent=True)
    else:
        raise ValueError(
            "boundary ingests CSV and notable-JSON batches only; "
            "other formats need a dedicated parser"
        )
    t1 = _utcnow() + timedelta(seconds=1)

    result = dict(stats)
    result["batch_id"] = manifest["batch_id"]
    result["ingest_window"] = [t0.isoformat(), t1.isoformat()]
    manifest["ingest"] = result
    manifest["ingest_window"] = result["ingest_window"]
    manifest["inserted_ids"] = result.get("inserted_ids") or []
    (staging_dir() / f"{manifest['batch_id']}-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return result


def ingest_json_notables(
    path: Union[str, Path],
    silent: bool = True,
    session_factory=None,
) -> Dict[str, Any]:
    """Canonical Splunk notable-JSON -> SplunkEvent loader.

    Accepts a JSON object or an array of objects, each representing one
    notable/event (e.g. an Incident Review JSON export). Every object is
    stored as one SplunkEvent whose ``raw`` is the full JSON payload, so
    downstream analysis sees the complete notable.

    Key mapping per object (all optional):
      - ``sourcetype`` (default ``splunk:notable``)
      - ``source`` (default ``splunk_json_export``)
      - ``host`` (default ``unknown``)
      - ``_time``/``timestamp``: ISO string or epoch seconds (Splunk exports
        epoch floats; both normalize to naive UTC, fallback = now)

    Deduplication: same (sourcetype, source, host, timestamp) candidates are
    additionally compared by raw payload hash, so same-second events do not
    collide. NOTE: this is event-level admission — the paste-box flow (with
    segmentation, sanitization, and promotion) remains the path for analyst
    pastes; this loader is for machine-exported JSON files.

    ``session_factory`` lets tests inject an isolated database.
    """
    import hashlib

    source = Path(path)
    stats: Dict[str, Any] = {
        "success": False,
        "rows_read": 0,
        "rows_inserted": 0,
        "rows_skipped": 0,
        "errors": [],
        "file": source.name,
    }
    if not source.is_file():
        stats["error"] = f"JSON file not found: {source}"
        return stats

    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        stats["error"] = f"invalid JSON: {exc}"
        return stats

    objects = payload if isinstance(payload, list) else [payload]
    if not objects or not all(isinstance(o, dict) for o in objects):
        stats["error"] = "JSON must be an object or an array of objects"
        return stats

    cap = int(os.environ.get("SPLUNK_BOUNDARY_MAX_EVENTS", "10000"))
    if len(objects) > cap:
        stats["error"] = f"{len(objects)} events exceeds SPLUNK_BOUNDARY_MAX_EVENTS={cap}"
        return stats

    SplunkEvent, SessionLocal = _load_db_module()
    session = (session_factory or SessionLocal)()
    inserted: list = []
    try:
        stats["success"] = True
        for index, obj in enumerate(objects, start=1):
            stats["rows_read"] += 1
            try:
                sourcetype = str(obj.get("sourcetype") or "splunk:notable")
                src = str(obj.get("source") or "splunk_json_export")
                host = str(obj.get("host") or "unknown")
                raw = json.dumps(obj, ensure_ascii=False)

                ts = None
                ts_value = obj.get("_time", obj.get("timestamp"))
                if isinstance(ts_value, (int, float)):
                    ts = datetime.fromtimestamp(float(ts_value), tz=timezone.utc).replace(tzinfo=None)
                elif isinstance(ts_value, str) and ts_value.strip():
                    try:
                        ts = datetime.fromisoformat(ts_value.strip().replace("Z", "+00:00"))
                        if ts.tzinfo is not None:
                            ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
                    except ValueError:
                        ts = None
                if ts is None:
                    ts = _utcnow()

                raw_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
                candidate = (
                    session.query(SplunkEvent)
                    .filter(
                        SplunkEvent.sourcetype == sourcetype,
                        SplunkEvent.source == src,
                        SplunkEvent.host == host,
                        SplunkEvent.timestamp == ts,
                    )
                    .all()
                )
                if any(
                    hashlib.sha1((e.raw or "").encode("utf-8")).hexdigest()[:16] == raw_hash
                    for e in candidate
                ):
                    stats["rows_skipped"] += 1
                    continue

                event = SplunkEvent(
                    sourcetype=sourcetype,
                    source=src,
                    host=host,
                    raw=raw[:2000],
                    timestamp=ts,
                )
                session.add(event)
                inserted.append(event)
                stats["rows_inserted"] += 1
                if stats["rows_inserted"] % 50 == 0:
                    session.commit()
            except Exception as exc:
                stats["rows_skipped"] += 1
                stats["errors"].append(f"Event {index}: {str(exc)[:100]}")
        session.commit()
        stats["inserted_ids"] = [obj.id for obj in inserted]

        if (
            stats["rows_read"] > 0
            and stats["rows_inserted"] == 0
            and stats["rows_skipped"] == len(stats["errors"])
        ):
            stats["success"] = False
            stats["error"] = (
                "all events failed (likely a database error); first: "
                + (stats["errors"][0] if stats["errors"] else "unknown")
            )
        return stats
    except Exception as exc:
        session.rollback()
        stats["success"] = False
        stats["error"] = str(exc)
        return stats
    finally:
        session.close()


def admit_file(path: Union[str, Path], source_label: str = "") -> Dict[str, Any]:
    """The one sanctioned entry point: validate -> quarantine -> ingest."""
    manifest = quarantine_file(path, source_label=source_label)
    stats = ingest_manifest(manifest)
    return {"manifest": manifest, "ingest": stats}


def list_batches() -> List[Dict[str, Any]]:
    out = []
    for path in sorted(staging_dir().glob("*-manifest.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def _delete_events_by_ids(ids: List[int]) -> int:
    """Delete exactly the SplunkEvents inserted by a batch. Isolated so the
    boundary logic stays testable without a database."""
    if not ids:
        return 0
    SplunkEvent, SessionLocal = _load_db_module()
    session = SessionLocal()
    try:
        deleted = (
            session.query(SplunkEvent)
            .filter(SplunkEvent.id.in_(ids))
            .delete(synchronize_session=False)
        )
        session.commit()
        return deleted
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _delete_events_in_window(window: List[str]) -> int:
    """Legacy fallback: delete SplunkEvents by ingest-time window (used only
    for manifests written before inserted_ids existed — windows can overlap
    between rapid consecutive batches, so id-based purge is preferred)."""
    SplunkEvent, SessionLocal = _load_db_module()
    start, end = (datetime.fromisoformat(w) for w in window)
    session = SessionLocal()
    try:
        deleted = (
            session.query(SplunkEvent)
            .filter(SplunkEvent.ingested_at >= start, SplunkEvent.ingested_at <= end)
            .delete(synchronize_session=False)
        )
        session.commit()
        return deleted
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def purge_batch(batch_id: str) -> Dict[str, Any]:
    """Remove a batch's staged file, manifest, and ingested DB rows."""
    if not _STAMP_RE.match(batch_id):
        raise ValueError("malformed batch id")

    manifest_path = staging_dir() / f"{batch_id}-manifest.json"
    window: Optional[List[str]] = None
    inserted_ids: Optional[List[int]] = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        window = manifest.get("ingest_window")
        raw_ids = manifest.get("inserted_ids")
        if isinstance(raw_ids, list):
            inserted_ids = [int(i) for i in raw_ids if isinstance(i, (int, float))]
        staged = Path(manifest.get("staged_path", ""))
        if staged.is_file():
            staged.unlink()
        manifest_path.unlink()

    if inserted_ids is not None:
        deleted_events = _delete_events_by_ids(inserted_ids)
    elif window:
        deleted_events = _delete_events_in_window(window)
    else:
        deleted_events = 0
    return {
        "batch_id": batch_id,
        "staged_removed": not manifest_path.is_file(),
        "events_deleted": deleted_events,
        "purge_strategy": "ids" if inserted_ids is not None else ("window" if window else "none"),
    }


def status() -> Dict[str, Any]:
    staging = staging_dir()
    batches = list_batches()
    return {
        "mode": _mode(),
        "staging_dir": str(staging),
        "max_bytes": max_bytes(),
        "batch_count": len(batches),
        "batches": [
            {
                "batch_id": b["batch_id"],
                "filename": b.get("validation", {}).get("filename"),
                "staged_at": b.get("staged_at"),
                "ingested_rows": (b.get("ingest") or {}).get("rows_inserted"),
            }
            for b in batches
        ],
    }
