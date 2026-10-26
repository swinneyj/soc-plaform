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
  ``SPLUNK_CA_BUNDLE``  optional path to a PEM CA bundle (or a CA directory:
                    plain PEMs or OpenSSL's hashed layout) used to verify the
                    management port's certificate.
                    This is the supported way to talk to an instance whose
                    certificate is signed by an internal/self-signed CA:
                    trust is widened for *this connection only*, never
                    disabled process-wide.
  ``SPLUNK_VERIFY_TLS``  optional ``false``/``0``/``no`` escape hatch for a
                    lab box whose certificate cannot be pinned. Verification
                    is then off for this connector only, and the disabled
                    state is logged and reported by /api/splunk/search-status
                    so it cannot be an invisible decision.

Precedence: a valid ``SPLUNK_CA_BUNDLE`` always wins (verification stays on).
``SPLUNK_VERIFY_TLS=false`` only applies when no bundle is configured, and
emits a warning naming the risk.

Unconfigured construction fails loudly (``SplunkSearchError``) instead of
silently pretending to search. The factory lives in
``services/search_backend.py``, which re-exports this class.
"""

import csv
import io
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("soc.splunk")

_FALSY = ("0", "false", "no", "off")


class SplunkSearchError(RuntimeError):
    """Raised when the Splunk REST call is unconfigured or fails."""


def _env_ca_bundle() -> str:
    return (os.environ.get("SPLUNK_CA_BUNDLE") or "").strip()


def _env_verify_disabled() -> bool:
    """True only on an explicit falsy ``SPLUNK_VERIFY_TLS``.

    Unset or any other value means "verify" — TLS verification is opt-out,
    never opt-in, so a typo cannot silently disable it.
    """
    return (os.environ.get("SPLUNK_VERIFY_TLS") or "").strip().lower() in _FALSY


def _check_ca_bundle(path: str) -> None:
    """A configured-but-missing bundle is a configuration error, not a
    mysterious handshake failure three weeks later."""
    if path and not os.path.exists(path):
        raise SplunkSearchError(
            "SPLUNK_CA_BUNDLE points at a path that does not exist: "
            f"{path!r}. Point it at the instance's PEM CA bundle (or its CA "
            "directory), or unset it to use the system trust store."
        )


# A pinned CA that expires inside this window is still usable, but it is close
# enough to a handshake failure that the status endpoint must say so before an
# analyst discovers it mid-investigation.
_CA_EXPIRY_WARN_DAYS = 30


def _ca_certificate_files(path: str, max_files: int = 500) -> List[str]:
    """Files to inspect: the bundle itself, or every file in a CA directory.

    Directory bundles come in two shapes: plain PEMs (``*.pem``) and OpenSSL's
    hashed layout (``9f86d081.0``) that ``requests``/urllib3 expect for a
    directory ``verify=`` value. Filtering by suffix would silently report
    "no certificates" for the hashed shape, so every regular file is tried and
    the unparseable ones (keys, junk) are skipped. Bounded so a system CA
    directory cannot make this endpoint slow.
    """
    if not os.path.isdir(path):
        return [path]
    files: List[str] = []
    for name in sorted(os.listdir(path)):
        candidate = os.path.join(path, name)
        if os.path.isfile(candidate):
            files.append(candidate)
            if len(files) >= max_files:
                break
    return files


def ca_bundle_expiry(path: str) -> Dict[str, Any]:
    """Earliest certificate expiry in a PEM CA bundle (file or directory).

    Returns ``{"certificates": n, "expires_at": datetime | None,
    "error": str, "checked": bool}``. The *earliest* expiry wins because that
    is the one that breaks verification first. ``error`` is non-empty only for
    a bundle that exists but holds no parseable certificate — a state that
    would fail the handshake anyway, so readiness must not stay green through
    it. A missing ``cryptography`` degrades to ``checked: False`` rather than
    failing readiness (the connector itself does not need the package).
    """
    try:
        from cryptography import x509
    except ImportError:  # pragma: no cover - cryptography is a pinned dep
        return {"certificates": 0, "expires_at": None, "error": "", "checked": False}

    expiries: List[Any] = []
    for cert_file in _ca_certificate_files(path):
        try:
            with open(cert_file, "rb") as fh:
                raw = fh.read()
        except OSError as exc:
            return {
                "certificates": 0,
                "expires_at": None,
                "error": f"SPLUNK_CA_BUNDLE is unreadable: {exc}",
                "checked": True,
            }
        try:
            certs = x509.load_pem_x509_certificates(raw)
        except ValueError:
            continue
        for cert in certs:
            try:
                expiries.append(cert.not_valid_after_utc)
            except AttributeError:  # cryptography < 42
                expiries.append(cert.not_valid_after.replace(tzinfo=timezone.utc))

    if not expiries:
        return {
            "certificates": 0,
            "expires_at": None,
            "error": "SPLUNK_CA_BUNDLE contains no parseable PEM certificate",
            "checked": True,
        }
    return {
        "certificates": len(expiries),
        "expires_at": min(expiries),
        "error": "",
        "checked": True,
    }


def tls_settings() -> Dict[str, Any]:
    """Resolve the connector's TLS posture from env without building a client.

    Used by ``/api/splunk/search-status`` so an operator can see the posture
    before a query runs — including how long the pinned CA has left. Reports
    booleans, counts and dates only: never the token or URL, and never the
    bundle path (local filesystem layout is not the API's business).
    """
    bundle = _env_ca_bundle()
    bundle_present = bool(bundle)
    bundle_ok = bundle_present and os.path.exists(bundle)
    # A bundle implies verification; the opt-out is ignored while one is set.
    disabled = _env_verify_disabled() and not bundle_present

    problem = ""
    warning = ""
    certificates = 0
    expires_at: Optional[str] = None
    days_remaining: Optional[int] = None
    expired = False

    if bundle_present and not bundle_ok:
        problem = f"SPLUNK_CA_BUNDLE path does not exist: {bundle!r}"
    elif bundle_ok:
        info = ca_bundle_expiry(bundle)
        certificates = info["certificates"]
        expiry = info["expires_at"]
        if info["error"]:
            problem = info["error"]
        elif expiry is not None:
            expires_at = expiry.astimezone(timezone.utc).isoformat()
            remaining = (expiry - datetime.now(timezone.utc)).total_seconds()
            days_remaining = int(remaining // 86400)
            expired = remaining <= 0
            if expired:
                problem = (
                    "SPLUNK_CA_BUNDLE contains a certificate that expired on "
                    f"{expiry.date().isoformat()} — the handshake will fail; "
                    "replace the bundle with a current one."
                )
            elif days_remaining <= _CA_EXPIRY_WARN_DAYS:
                warning = (
                    f"SPLUNK_CA_BUNDLE expires in {days_remaining} day(s) "
                    f"({expiry.date().isoformat()}) — rotate it before the "
                    "handshake starts failing."
                )

    return {
        "verify": not disabled,
        "verification_disabled": disabled,
        "ca_bundle_configured": bundle_present,
        "ca_bundle_readable": bundle_ok,
        "ca_bundle_certificates": certificates,
        "ca_bundle_expires_at": expires_at,
        "ca_bundle_days_remaining": days_remaining,
        "ca_bundle_expired": expired,
        "ca_bundle_warning": warning,
        "problem": problem,
    }


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
        ca_bundle: Optional[str] = None,
        verify_tls: Optional[bool] = None,
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
        self._verify: Any = self._resolve_verify(ca_bundle, verify_tls)

    @staticmethod
    def _resolve_verify(ca_bundle: Optional[str], verify_tls: Optional[bool]) -> Any:
        """Turn the TLS env/args into the ``verify=`` value requests expects.

        ``True`` (certifi default) or a CA bundle path. Deliberately never
        returns a trust-store override that disables verification globally —
        an opt-out affects only this connector instance.
        """
        bundle = (ca_bundle if ca_bundle is not None else _env_ca_bundle()).strip()
        if bundle:
            _check_ca_bundle(bundle)
            if verify_tls is False or _env_verify_disabled():
                logger.warning(
                    "Splunk TLS: SPLUNK_VERIFY_TLS=false ignored because "
                    "SPLUNK_CA_BUNDLE is set — verifying against the bundle."
                )
            return bundle
        disabled = (
            verify_tls is False
            if verify_tls is not None
            else _env_verify_disabled()
        )
        if disabled:
            logger.warning(
                "Splunk TLS verification is DISABLED for %s. Any certificate "
                "is accepted for this connector. Prefer SPLUNK_CA_BUNDLE "
                "(pin the instance's CA) and report this state in the "
                "security tracker.",
                "the configured SPLUNK_URL",
            )
            return False
        return True

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

    def _tls_failure_message(self, exc: Exception) -> str:
        """A certificate failure is a config problem, so name the fix.

        The raw OpenSSL text (``CERTIFICATE_VERIFY_FAILED``) tells an operator
        nothing about which knob to turn; this does.
        """
        hint = (
            "Splunk TLS verification failed: "
            f"{exc}. Most management ports (8089) serve a certificate signed "
            "by an internal or self-signed CA. Set SPLUNK_CA_BUNDLE to that "
            "CA's PEM bundle (preferred), or install the CA in this host's "
            "trust store. SPLUNK_VERIFY_TLS=false exists as a last-resort lab "
            "escape hatch and is reported by /api/splunk/search-status."
        )
        return hint

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
                verify=self._verify,
            )
        except requests.exceptions.SSLError as exc:
            raise SplunkSearchError(self._tls_failure_message(exc)) from exc
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
                verify=self._verify,
            )
        except requests.exceptions.SSLError as exc:
            raise SplunkSearchError(self._tls_failure_message(exc)) from exc
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
