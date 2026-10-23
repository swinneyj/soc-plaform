"""
Pluggable search-backend abstraction for the SOC Platform (Phase 3).

A SearchBackend executes a query against a search system and returns
normalized rows. Two implementations ship:

- ``MockSplunkBackend`` (``services/splunk_mock_backend.py``): deterministic
  in-memory search over the committed seed corpus — no credentials, no network.
- ``RealSplunkBackend`` (``services/splunk_real_backend.py``): read-only Splunk
  REST connector — search jobs via ``/services/search/jobs/export`` plus a
  visible-saved-search listing, both bearer-authenticated.

Choose with the ``SEARCH_BACKEND`` environment variable::

    SEARCH_BACKEND=mock    (default; no credentials, fully deterministic)
    SEARCH_BACKEND=splunk  (requires ``SPLUNK_URL`` + ``SPLUNK_TOKEN``)

This module is the single public import surface: both backend classes and
``SplunkSearchError`` are re-exported here, so existing imports keep working
across the Phase 3 split. The factory lives here next to the pure
job-outcome helpers (``map_search_outcome`` / ``summarize_search_rows``).

Normalized event rows match the ``SplunkEvent`` model columns so callers
can persist results directly: ``source``, ``sourcetype``, ``host``,
``timestamp`` (ISO string), ``raw``.
"""

import os
from typing import Any, Dict, List, Optional

from services.splunk_mock_backend import MockSplunkBackend  # noqa: F401 (re-export)
from services.splunk_mock_backend import _parse_offset  # noqa: F401 (re-export)
from services.splunk_real_backend import RealSplunkBackend  # noqa: F401 (re-export)
from services.splunk_real_backend import SplunkSearchError  # noqa: F401 (re-export)

__all__ = [
    "MockSplunkBackend",
    "RealSplunkBackend",
    "SplunkSearchError",
    "get_search_backend",
    "map_search_outcome",
    "summarize_search_rows",
]


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
