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
    """Quarantine directory (created on demand), env-overridable."""
    root = Path(
        os.environ.get("SPLUNK_BOUNDARY_STAGING")
        or (Path(__file__).resolve().parent.parent / "Data" / "quarantine")
    )
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

                    session.add(
                        SplunkEvent(
                            sourcetype=sourcetype,
                            source=src,
                            host=host,
                            raw=raw[:2000],
                            timestamp=ts,
                        )
                    )
                    stats["rows_inserted"] += 1
                    if stats["rows_inserted"] % 50 == 0:
                        session.commit()
                except Exception as exc:
                    stats["rows_skipped"] += 1
                    stats["errors"].append(f"Row {row_num}: {str(exc)[:100]}")
            session.commit()

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
    if not staged.name.endswith(".csv"):
        raise ValueError(
            "boundary ingests CSV batches only; other formats need a dedicated parser"
        )

    t0 = _utcnow() - timedelta(seconds=1)
    stats = ingest_csv_events(str(staged), silent=True)
    t1 = _utcnow() + timedelta(seconds=1)

    result = dict(stats)
    result["batch_id"] = manifest["batch_id"]
    result["ingest_window"] = [t0.isoformat(), t1.isoformat()]
    manifest["ingest"] = result
    manifest["ingest_window"] = result["ingest_window"]
    (staging_dir() / f"{manifest['batch_id']}-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return result


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


def _delete_events_in_window(window: List[str]) -> int:
    """Delete SplunkEvents ingested within the batch window. Isolated so the
    boundary logic stays testable without a database."""
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
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        window = manifest.get("ingest_window")
        staged = Path(manifest.get("staged_path", ""))
        if staged.is_file():
            staged.unlink()
        manifest_path.unlink()

    deleted_events = _delete_events_in_window(window) if window else 0
    return {
        "batch_id": batch_id,
        "staged_removed": not manifest_path.is_file(),
        "events_deleted": deleted_events,
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
