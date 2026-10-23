"""
Read-only Splunk REST connector (Phase 3 real integration).

RealSplunkBackend talks to Splunk's management port over HTTPS with a bearer
token and only ever **reads**:

- ``POST {SPLUNK_URL}/services/search/jobs/export`` — the synchronous export
  endpoint runs one search and streams results in a single round-trip (no
  job-create/poll loop), and normalizes each row to the shape the mock returns;
- ``GET {SPLUNK_URL}/services/saved/searches`` — lists the visible saved
  searches so an analyst can pick an existing SPL rather than retyping one.

Configuration (BWS/keychain-injected env, never stored in the DB):
  ``SPLUNK_URL``    base URL, e.g. ``https://splunk.example.com:8089``
  ``SPLUNK_TOKEN``  bearer token (``search`` capability only — no admin,
                    no index writes, no saved-search editing)

Unconfigured construction fails loudly (``SplunkSearchError``) instead of
silently pretending to search. The factory lives in
``services/search_backend.py``, which re-exports this class.
"""

import csv
import io
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class SplunkSearchError(RuntimeError):
    """Raised when the Splunk REST call is unconfigured or fails."""


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

    def _request_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

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
                headers=self._request_headers(),
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

    def list_saved_searches(self, limit: int = 200) -> List[Dict[str, Any]]:
        """List the instance's visible saved searches (read-only GET).

        Returns ``[{"name", "description", "search"}, ...]`` sorted by name.
        Saved searches marked ``isvisible: false`` are omitted. Read-only by
        construction: the connector never has the ``edit_saved_searches``
        capability, and this method issues a single GET.
        """
        import requests

        session = self._session or requests.Session()
        try:
            response = session.get(
                f"{self._base_url}/services/saved/searches",
                headers=self._request_headers(),
                params={"count": "-1", "output_mode": "json"},
                timeout=self._timeout,
            )
        except requests.RequestException as exc:
            raise SplunkSearchError(f"Splunk request failed: {exc}") from exc
        if response.status_code != 200:
            snippet = (response.text or "").strip().replace("\n", " ")[:200]
            raise SplunkSearchError(
                "Splunk saved-search listing failed with HTTP "
                f"{response.status_code}: {snippet}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise SplunkSearchError(
                "Splunk saved-search listing returned non-JSON content"
            ) from exc
        items: List[Dict[str, Any]] = []
        for entry in (payload or {}).get("entry") or []:
            name = str(entry.get("name") or "").strip()
            content = entry.get("content") or {}
            if not name:
                continue
            # Splunk returns booleans as true/false in JSON; older/odd exports
            # may stringify them. Treat every falsy spelling as invisible.
            visible = content.get("isvisible", True)
            if visible in (False, 0, "0", "false", "False"):
                continue
            items.append(
                {
                    "name": name,
                    "description": str(content.get("description") or "").strip(),
                    "search": str(content.get("search") or "").strip(),
                }
            )
        items.sort(key=lambda item: item["name"].lower())
        return items[:limit]

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
