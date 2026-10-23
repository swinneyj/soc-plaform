#!/usr/bin/env python3
"""Fake Splunk management-port API — for local development and tests only.

**Not Splunk.** This is a stdlib-only stand-in that speaks the tiny slice of
the Splunk REST API this platform actually calls, so `RealSplunkBackend` and
the `/api/splunk/*` routes can be exercised end-to-end over real HTTP without
a licensed instance or credentials:

  POST /services/search/jobs/export   -> CSV rows for the SPL subset the mock
                                         backend supports (executed by
                                         MockSplunkBackend over the committed
                                         seed corpus; `search ` prefix stripped,
                                         `_time` emitted as epoch seconds so the
                                         client's epoch -> naive-UTC
                                         normalization is exercised for real)
  GET  /services/saved/searches       -> JSON entry list, including one hidden
                                         entry so visibility filtering is
                                         exercised
  GET  /services/server/info          -> connectivity probe

Every /services/* request needs `Authorization: Bearer <token>`; a missing or
wrong token returns 401, so the connector's failure path is reachable.

Scope guard: this proves *our* client, never Splunk's real contract. It
executes the same bounded SPL subset as the mock backend over fixture data.
`docs/SPLUNK_REHEARSAL.md` still requires the real instance for its exit
criteria — this only de-risks that run.

Usage (dev):
  python scripts/fake_splunk.py --port 18089 --token dev-token
  # then, in the shell that launches the platform:
  SEARCH_BACKEND=splunk SPLUNK_URL=http://127.0.0.1:18089 \
    SPLUNK_TOKEN=dev-token bash scripts/start

Usage (tests): `FakeSplunkServer(...)` on an ephemeral port, as a context
manager or start()/stop() pair.
"""

import argparse
import csv
import io
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from services.search_backend import MockSplunkBackend  # noqa: E402

# Fixture saved searches: one visible, one hidden — the connector must return
# only the visible one, sorted by name.
SAVED_SEARCHES = (
    {
        "name": "Failed Logons (Fake)",
        "content": {
            "description": "fixture: failed SSH logons",
            "search": "sourcetype=linux_secure action=failure",
            "isvisible": True,
        },
    },
    {
        "name": "Hidden Operational Search",
        "content": {"description": "internal only", "search": "index=internal", "isvisible": False},
    },
)


def _epoch_seconds(timestamp):
    """Seed timestamps are naive local-naive ISO; treat them as UTC so the
    round-trip epoch -> naive-UTC in RealSplunkBackend._normalize_row returns
    the same instant the fixture described."""
    try:
        parsed = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _csv_rows(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["_time", "source", "sourcetype", "host", "raw"])
    for row in rows:
        writer.writerow(
            [
                _epoch_seconds(row.get("timestamp", "")),
                row.get("source", ""),
                row.get("sourcetype", ""),
                row.get("host", ""),
                row.get("raw", ""),
            ]
        )
    return buf.getvalue()


class _FakeSplunkHandler(BaseHTTPRequestHandler):
    server_version = "FakeSplunk/1.0"

    # Quiet by default so test output stays readable; --verbose turns it on.
    def log_message(self, fmt, *args):
        if getattr(self.server, "verbose", False):
            sys.stderr.write("fake-splunk: " + (fmt % args) + "\n")

    def _send(self, status, body, content_type):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        return self.headers.get("Authorization", "") == "Bearer " + self.server.token

    def do_GET(self):
        if not self._authorized():
            return self._send(401, "In handler 'export': Unauthorized", "text/plain; charset=utf-8")
        path = urlparse(self.path).path.rstrip("/")
        if path == "/services/saved/searches":
            payload = {"entry": [{"name": item["name"], "content": item["content"]} for item in SAVED_SEARCHES]}
            return self._send(200, json.dumps(payload), "application/json")
        if path == "/services/server/info":
            return self._send(
                200,
                json.dumps({"entry": [{"content": {"version": "fake-splunk-1.0"}}]}),
                "application/json",
            )
        return self._send(404, "Not Found", "text/plain; charset=utf-8")

    def do_POST(self):
        if not self._authorized():
            return self._send(401, "In handler 'export': Unauthorized", "text/plain; charset=utf-8")
        path = urlparse(self.path).path.rstrip("/")
        if path != "/services/search/jobs/export":
            return self._send(404, "Not Found", "text/plain; charset=utf-8")

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode("utf-8") if length else ""
        form = parse_qs(body)

        search = (form.get("search", [""])[0] or "").strip()
        if search.lower().startswith("search "):
            search = search[len("search "):]
        earliest = form.get("earliest_time", ["-7d"])[0] or "-7d"
        latest = form.get("latest_time", ["now"])[0] or "now"
        try:
            limit = max(1, int(form.get("max_count", ["500"])[0]))
        except (TypeError, ValueError):
            limit = 500

        try:
            rows = self.server.backend.search(search, earliest=earliest, latest=latest, limit=limit)
        except ValueError as exc:  # unsupported SPL operator -> Splunk-style 400
            return self._send(400, str(exc), "text/plain; charset=utf-8")
        return self._send(200, _csv_rows(rows), "text/csv; charset=utf-8")


class FakeSplunkServer:
    """Threaded fake on 127.0.0.1 (ephemeral port by default).

    `token` is a dummy development value, never a credential. The server binds
    loopback only and implements read endpoints exclusively.
    """

    def __init__(self, token="dev-token", seed_path=None, port=0, verbose=False):
        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), _FakeSplunkHandler)
        self._httpd.token = token
        self._httpd.verbose = verbose
        self._httpd.backend = MockSplunkBackend(seed_path=seed_path)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self._httpd.server_address[1]

    @property
    def base_url(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def serve_forever(self):
        """Blocking mode for the CLI (Ctrl-C to stop)."""
        self._httpd.serve_forever()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fake Splunk REST API (development only — not Splunk).")
    parser.add_argument("--port", type=int, default=18089)
    parser.add_argument("--token", default="dev-token", help="bearer token clients must send (dummy value)")
    parser.add_argument("--seed", default=None, help="seed_events.jsonl path (default: committed corpus)")
    parser.add_argument("--verbose", action="store_true", help="log requests to stderr")
    args = parser.parse_args(argv)

    server = FakeSplunkServer(token=args.token, seed_path=args.seed, port=args.port, verbose=args.verbose)
    print("fake Splunk listening on %s" % server.base_url)
    print("point the platform at it with:")
    print("  SEARCH_BACKEND=splunk SPLUNK_URL=%s SPLUNK_TOKEN=%s" % (server.base_url, args.token))
    print("(dummy token — never a real credential. Ctrl-C to stop.)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == "__main__":
    main()
