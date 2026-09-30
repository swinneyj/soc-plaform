"""
Pluggable search-backend abstraction for the SOC Platform (Phase 3).

A SearchBackend executes a query against a search system and returns
normalized rows. Two implementations ship:

- MockSplunkBackend: deterministic in-memory search over seed events loaded
  from ``services/mock_splunk/seed/seed_events.jsonl``. Supports the subset
  of SPL the platform's supportive queries actually use: base-term and
  key=value search, ``| search k=v``, ``| where k OP v`` comparisons,
  ``| stats count (by field)``, and ``| head N``.
- RealSplunkBackend: read-only Splunk REST connector. Executes SPL via the
  synchronous ``/services/search/jobs/export`` endpoint and normalizes rows
  to the same shape. Requires ``SPLUNK_URL`` + ``SPLUNK_TOKEN`` (bearer);
  unconfigured construction fails loudly instead of silently pretending.

Choose with the ``SEARCH_BACKEND`` environment variable::

    SEARCH_BACKEND=mock    (default; no credentials, fully deterministic)
    SEARCH_BACKEND=splunk  (requires the Phase 3 Splunk REST wiring)

Normalized event rows match the ``SplunkEvent`` model columns so callers
can persist results directly: ``source``, ``sourcetype``, ``host``,
``timestamp`` (ISO string), ``raw``.
"""

import csv
import io
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SEED_PATH = Path(__file__).parent / "mock_splunk" / "seed" / "seed_events.jsonl"

_TIME_RE = re.compile(r"^-?(?P<n>\d+)(?P<unit>[mhd])$")
_KV_RE = re.compile(r'(?P<key>\w+)=("(?P<qval>[^"]+)"|(?P<val>\S+))')


def _parse_offset(text: str) -> Optional[timedelta]:
    """Parse SPL-style time offsets (``-24h``, ``-15m``, ``-7d``).

    Returns the signed offset *from now*: past offsets are negative so that
    ``now + offset`` yields the absolute boundary time.
    """
    match = _TIME_RE.match(text.strip())
    if not match:
        return None
    n = int(match.group("n"))
    unit = {"m": "minutes", "h": "hours", "d": "days"}[match.group("unit")]
    return timedelta(**{unit: -n})


class MockSplunkBackend:
    """Deterministic search over the committed seed-event file."""

    def __init__(self, seed_path: Optional[Path] = None):
        self._seed_path = Path(seed_path) if seed_path else _SEED_PATH
        self._events = self._load_events()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def _load_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        with open(self._seed_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                record["timestamp"] = datetime.fromisoformat(record["timestamp"])
                events.append(record)
        events.sort(key=lambda e: e["timestamp"])
        return events

    # ------------------------------------------------------------------
    # SPL parsing (bounded, honest subset)
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_spl(
        spl: str,
    ) -> Tuple[Dict[str, str], List[str], List[Dict[str, Any]], str, str]:
        """Split into base kv-terms, free-text terms, pipeline ops, and any
        ``earliest=``/``latest=`` search-time qualifiers embedded in the SPL.

        Re-check variants re-scope their time window inline (``earliest=-4h``,
        see ``_rescope_variant_spl`` in api/main.py). Treating those
        qualifiers as field-match terms (``earliest == "-4h"``) would
        silently match no events, so they are consumed here and applied as
        the search window instead — matching real Splunk, where an explicit
        ``earliest=`` in the search string overrides the job default.
        """
        stages = [s.strip() for s in spl.split("|") if s.strip()]
        base_terms: Dict[str, str] = {}
        free_terms: List[str] = []
        embedded_earliest = ""
        embedded_latest = ""
        for key, _whole, qval, val in _KV_RE.findall(stages[0]):
            value = qval or val
            if key == "earliest":
                embedded_earliest = value
                continue
            if key == "latest":
                embedded_latest = value
                continue
            base_terms[key] = value
        base_free = _KV_RE.sub("", stages[0])
        free_terms = [t for t in base_free.split() if t]
        ops = []
        for stage in stages[1:]:
            parts = stage.split(None, 1)
            op_name = parts[0].lower()
            op_args = parts[1] if len(parts) > 1 else ""
            ops.append({"op": op_name, "args": op_args})
        return base_terms, free_terms, ops, embedded_earliest, embedded_latest

    @staticmethod
    def _fields(raw: str) -> Dict[str, str]:
        """Extract key=value pairs from a raw event body."""
        out: Dict[str, str] = {}
        for key, _whole, qval, val in _KV_RE.findall(raw or ""):
            out[key] = qval or val
        return out

    @staticmethod
    def _in_time_window(event_ts: datetime, earliest: str, latest: str) -> bool:
        now = datetime.now()
        low = None
        off = _parse_offset(earliest or "")
        if off:
            low = now + off  # earliest=-24h -> now minus 24h
        elif earliest and earliest not in ("all", "0"):
            try:
                low = datetime.fromisoformat(earliest)
            except ValueError:
                low = None
        high = now if (not latest or latest == "now") else None
        if latest and latest != "now":
            off = _parse_offset(latest)
            if off:
                high = now + off
            else:
                try:
                    high = datetime.fromisoformat(latest)
                except ValueError:
                    high = None
        if low and event_ts < low:
            return False
        if high and event_ts > high:
            return False
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def search(
        self,
        spl: str,
        earliest: str = "-7d",
        latest: str = "now",
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        base_terms, free_terms, ops, spl_earliest, spl_latest = self._parse_spl(spl)
        # An explicit qualifier inside the SPL text wins over the caller's
        # job-level window (same precedence as real Splunk).
        earliest = spl_earliest or earliest
        latest = spl_latest or latest

        rows: List[Dict[str, Any]] = []
        for event in self._events:
            if not self._in_time_window(event["timestamp"], earliest, latest):
                continue
            fields = self._fields(event.get("raw", ""))
            fields.update(
                {
                    "source": event.get("source", ""),
                    "sourcetype": event.get("sourcetype", ""),
                    "host": event.get("host", ""),
                }
            )
            if any(fields.get(k) != v for k, v in base_terms.items()):
                continue
            blob = " ".join(
                [event.get("raw", ""), event.get("source", ""),
                 event.get("sourcetype", ""), event.get("host", "")]
            )
            if any(term not in blob for term in free_terms):
                continue
            rows.append(
                {
                    "source": event.get("source", ""),
                    "sourcetype": event.get("sourcetype", ""),
                    "host": event.get("host", ""),
                    "timestamp": event["timestamp"].isoformat(),
                    "raw": event.get("raw", ""),
                    "_fields": fields,
                }
            )

        stats_field = None
        head = limit
        where_preds = []
        for op in ops:
            if op["op"] == "search":
                for key, _whole, qval, val in _KV_RE.findall(op["args"]):
                    where_preds.append((key, "==", qval or val))
            elif op["op"] == "where":
                for predicate in op["args"].split(" AND "):
                    match = re.match(
                        r"(\w+)\s*(==|=|!=|>=|<=|>|<)\s*(\"[^\"]+\"|\S+)",
                        predicate.strip(),
                    )
                    if match:
                        field, oper, value = match.groups()
                        where_preds.append((field, oper, value.strip('"')))
            elif op["op"] == "stats":
                args = op["args"]
                if "by" in args:
                    stats_field = args.split("by", 1)[1].strip()
            elif op["op"] == "head":
                try:
                    head = min(head, int(op["args"].split()[0]))
                except (ValueError, IndexError):
                    pass
            else:
                raise ValueError(
                    f"MockSplunkBackend: unsupported SPL operator '|{op['op']}'"
                )

        def _keep(row: Dict[str, Any]) -> bool:
            for field, oper, expected in where_preds:
                actual = row["_fields"].get(field)
                if actual is None:
                    return False
                if oper in ("=", "=="):
                    if actual != expected:
                        return False
                elif oper == "!=":
                    if actual == expected:
                        return False
                else:
                    # Numeric-only operators; non-numeric values never match.
                    try:
                        a_num, e_num = float(actual), float(expected)
                    except ValueError:
                        return False
                    if oper == ">" and not a_num > e_num:
                        return False
                    if oper == "<" and not a_num < e_num:
                        return False
                    if oper == ">=" and not a_num >= e_num:
                        return False
                    if oper == "<=" and not a_num <= e_num:
                        return False
            return True

        rows = [r for r in rows if _keep(r)]

        if stats_field is not None:
            counts: Dict[str, int] = {}
            for row in rows:
                counts[row["_fields"].get(stats_field, "(unknown)")] = (
                    counts.get(row["_fields"].get(stats_field, "(unknown)"), 0) + 1
                )
            rows = [
                {stats_field: value, "count": count}
                for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
        else:
            rows = rows[-head:] if head else []
            for row in rows:
                row.pop("_fields", None)
            rows.reverse()  # newest first, like Splunk

        return rows[:limit]

    def execute_for_case(
        self,
        case_id: str,
        rule_id: str,
        query_title: str,
        spl: str,
        earliest: str = "-7d",
        latest: str = "now",
    ) -> Dict[str, Any]:
        """Run one supportive query and return the persisted-result shape."""
        rows = self.search(spl, earliest=earliest, latest=latest)
        return {
            "case_id": case_id,
            "rule_id": rule_id,
            "query_title": query_title,
            "source_system": "splunk",
            "raw_result": json.dumps(
                {
                    "backend": "mock",
                    "spl": spl,
                    "earliest": earliest,
                    "latest": latest,
                    "row_count": len(rows),
                    "rows": rows,
                }
            ),
        }


class SplunkSearchError(RuntimeError):
    """Raised when the Splunk REST search is unconfigured or fails."""


class RealSplunkBackend:
    """Read-only Splunk REST connector (Phase 3 real integration).

    Executes SPL through ``POST {SPLUNK_URL}/services/search/jobs/export`` —
    the synchronous export endpoint runs the search and streams results in a
    single round-trip (no job-create/poll loop) — and normalizes each result
    row to the shape the mock returns.

    Configuration (BWS/keychain-injected env, never stored in the DB):
      ``SPLUNK_URL``    base URL, e.g. ``https://splunk.example.com:8089``
      ``SPLUNK_TOKEN``  bearer token
    """

    _NO_PREFIX = ("search ", "savedsearch ", "loadjob ", "rest ", "makeresults ")

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
        session: Any = None,
    ):
        self._base_url = (base_url or os.environ.get("SPLUNK_URL") or "").strip().rstrip("/")
        self._token = (token or os.environ.get("SPLUNK_TOKEN") or "").strip()
        self._timeout = float(
            timeout or os.environ.get("SEARCH_ONE_TIMEOUT_SECONDS", "60")
        )
        # Test seam: a duck-typed requests.Session. Real runs create one lazily
        # so the mock path stays dependency-free at import time.
        self._session = session
        if not self._base_url or not self._token:
            raise SplunkSearchError(
                "RealSplunkBackend is not configured: set SPLUNK_URL and "
                "SPLUNK_TOKEN (BWS/keychain-injected env), or use "
                "SEARCH_BACKEND=mock."
            )

    @classmethod
    def _export_search_expr(cls, spl: str) -> str:
        """Splunk search strings need a generating command; the platform's
        templates are bare term searches, so prepend ``search`` unless the
        SPL already starts with a pipeline or generating command."""
        stripped = (spl or "").strip()
        if stripped.startswith("|") or stripped.lower().startswith(cls._NO_PREFIX):
            return stripped
        return "search " + stripped

    @staticmethod
    def _normalize_row(row: Dict[str, Any]) -> Dict[str, str]:
        fields = {
            str(k): ("" if v is None else str(v))
            for k, v in row.items()
            if k is not None
        }
        raw_time = fields.get("_time", "")
        timestamp = ""
        if raw_time:
            try:
                timestamp = (
                    datetime.fromtimestamp(float(raw_time), tz=timezone.utc)
                    .replace(tzinfo=None)
                    .isoformat()
                )
            except (ValueError, OSError, OverflowError):
                timestamp = ""
        for key in ("source", "sourcetype", "host", "raw"):
            fields.setdefault(key, "")
        fields["timestamp"] = timestamp
        return fields

    def search(
        self,
        spl: str,
        earliest: str = "-7d",
        latest: str = "now",
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        import requests

        payload = {
            "search": self._export_search_expr(spl),
            "earliest_time": earliest,
            "latest_time": latest,
            "max_count": limit,
            "output_mode": "csv",
        }
        session = self._session or requests.Session()
        try:
            response = session.post(
                f"{self._base_url}/services/search/jobs/export",
                headers={"Authorization": f"Bearer {self._token}"},
                data=payload,
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise SplunkSearchError(f"Splunk request failed: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip().replace("\n", " ")[:200]
            raise SplunkSearchError(
                f"Splunk export failed with HTTP {response.status_code}: {snippet}"
            )
        reader = csv.DictReader(io.StringIO(response.text or ""))
        rows = [self._normalize_row(row) for row in reader]
        return rows[:limit]

    def execute_for_case(
        self,
        case_id: str,
        rule_id: str,
        query_title: str,
        spl: str,
        earliest: str = "-7d",
        latest: str = "now",
    ) -> Dict[str, Any]:
        """Run one supportive query and return the persisted-result shape."""
        rows = self.search(spl, earliest=earliest, latest=latest)
        return {
            "case_id": case_id,
            "rule_id": rule_id,
            "query_title": query_title,
            "source_system": "splunk",
            "raw_result": json.dumps(
                {
                    "backend": "splunk",
                    "spl": spl,
                    "earliest": earliest,
                    "latest": latest,
                    "row_count": len(rows),
                    "rows": rows,
                }
            ),
        }


def get_search_backend(name: Optional[str] = None):
    """Factory: resolve the configured backend (env ``SEARCH_BACKEND``)."""
    chosen = (name or os.environ.get("SEARCH_BACKEND", "mock")).strip().lower()
    if chosen == "mock":
        return MockSplunkBackend()
    if chosen == "splunk":
        return RealSplunkBackend()
    raise ValueError(f"Unknown SEARCH_BACKEND '{chosen}' (expected 'mock' or 'splunk')")


# ---------------------------------------------------------------------------
# Job-outcome mapping and result summarization (pure)
# ---------------------------------------------------------------------------

def map_search_outcome(row_count: Optional[int], error: Optional[str] = None) -> str:
    """Map a search-job outcome onto the evidence ledger ``result_status`` vocabulary.

    Contract (plan §5): 0 rows ⇒ ``no_results``; error (including timeout or
    unsupported SPL) ⇒ ``query_failed``; anything else ⇒ ``success``.
    """
    if error:
        return "query_failed"
    if (row_count or 0) <= 0:
        return "no_results"
    return "success"


def summarize_search_rows(
    rows: Optional[List[Dict[str, Any]]],
    error: Optional[str] = None,
    max_rows: int = 20,
    max_chars: int = 4000,
) -> str:
    """Build a bounded, human-reviewable result summary for the evidence ledger."""
    if error:
        return f"Search failed: {error}"[:max_chars]

    rows = rows or []
    if not rows:
        return "0 rows returned (no matching events in the selected time window)."

    lines: List[str] = [f"{len(rows)} row(s) returned (showing first {min(len(rows), max_rows)}):"]
    for row in rows[:max_rows]:
        raw = row.get("raw")
        if raw:
            lines.append(str(raw))
        else:
            lines.append(" ".join(f"{k}={v}" for k, v in row.items() if not k.startswith("_")))
    if len(rows) > max_rows:
        lines.append(f"... {len(rows) - max_rows} more row(s) omitted.")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n... (summary truncated)"
    return text
