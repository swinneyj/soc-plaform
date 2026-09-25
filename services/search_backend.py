"""
Pluggable search-backend abstraction for the SOC Platform (Phase 3).

A SearchBackend executes a query against a search system and returns
normalized rows. Two implementations ship:

- MockSplunkBackend: deterministic in-memory search over seed events loaded
  from ``services/mock_splunk/seed/seed_events.jsonl``. Supports the subset
  of SPL the platform's supportive queries actually use: base-term and
  key=value search, ``| search k=v``, ``| where k OP v`` comparisons,
  ``| stats count (by field)``, and ``| head N``.
- RealSplunkBackend: intentionally unimplemented until the Splunk REST
  integration lands. Selecting it fails loudly instead of silently
  pretending.

Choose with the ``SEARCH_BACKEND`` environment variable::

    SEARCH_BACKEND=mock    (default; no credentials, fully deterministic)
    SEARCH_BACKEND=splunk  (requires the Phase 3 Splunk REST wiring)

Normalized event rows match the ``SplunkEvent`` model columns so callers
can persist results directly: ``source``, ``sourcetype``, ``host``,
``timestamp`` (ISO string), ``raw``.
"""

import json
import os
import re
from datetime import datetime, timedelta
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
    def _parse_spl(spl: str) -> Tuple[Dict[str, str], List[str], List[Dict[str, Any]]]:
        """Split into base kv-terms, free-text terms, and pipeline ops."""
        stages = [s.strip() for s in spl.split("|") if s.strip()]
        base_terms: Dict[str, str] = {}
        free_terms: List[str] = []
        for key, _whole, qval, val in _KV_RE.findall(stages[0]):
            base_terms[key] = qval or val
        base_free = _KV_RE.sub("", stages[0])
        free_terms = [t for t in base_free.split() if t]
        ops = []
        for stage in stages[1:]:
            parts = stage.split(None, 1)
            op_name = parts[0].lower()
            op_args = parts[1] if len(parts) > 1 else ""
            ops.append({"op": op_name, "args": op_args})
        return base_terms, free_terms, ops

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
        base_terms, free_terms, ops = self._parse_spl(spl)

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


class RealSplunkBackend:
    """Placeholder for the Phase 3 Splunk REST integration."""

    def __init__(self, *args: Any, **kwargs: Any):
        raise NotImplementedError(
            "RealSplunkBackend is not wired yet. Set SEARCH_BACKEND=mock for "
            "local development, or implement the Splunk REST connector."
        )

    def search(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        raise NotImplementedError


def get_search_backend(name: Optional[str] = None):
    """Factory: resolve the configured backend (env ``SEARCH_BACKEND``)."""
    chosen = (name or os.environ.get("SEARCH_BACKEND", "mock")).strip().lower()
    if chosen == "mock":
        return MockSplunkBackend()
    if chosen == "splunk":
        return RealSplunkBackend()
    raise ValueError(f"Unknown SEARCH_BACKEND '{chosen}' (expected 'mock' or 'splunk')")
