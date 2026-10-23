#!/usr/bin/env python3
"""Live security posture probe — the pre-push gate's runtime half.

The static gates (pin markers, compile, hygiene, option keys) cannot see the
regression this exists for: an API that is reachable but *ungated*. That is
exactly how tracker items #5/#6/#7/#14 got found — by probing a running
server, not by reading code. This script asserts the posture the tracker
records as closed, against the local API:

  1. health answers 200                              (the right server)
  2. unauthenticated reads are refused               (tracker #5, session mode)
  3. unauthenticated mutations are refused           (tracker #6)
  4. /docs and /openapi.json are 404                 (tracker #14)
  5. CORS preflight has no wildcard method/header/origin (tracker #7)
  6. /api/auth/session refuses an anonymous caller   (no identity leak)

Mutation probes use empty bodies and nonexistent ids on purpose: a bypassed
gate would answer 422/404 (never a mutation), so the probe cannot damage
data even when it is the thing detecting damage.

The API is a deliberate on-demand service (`scripts/start`), so an absent
API is a SKIP, not a failure — a hook that demanded a running server would
block every push after a reboot. Connection refused => skip; anything the
server *does* answer is judged.

Exit 0: all probes pass, or the API is down (skipped).
Exit 1: a probe failed. Fix it, or bypass deliberately: git push --no-verify

Env:
  SECURITY_SMOKE_BASE          default http://127.0.0.1:8000
  SECURITY_SMOKE_ALLOW_OPEN=1  permit flag-off mode (reads open by contract)
  SECURITY_SMOKE_ALLOW_DOCS=1  permit ENABLE_DOCS=1 (docs exposed on purpose)
"""

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("SECURITY_SMOKE_BASE", "http://127.0.0.1:8000").rstrip("/")
ALLOW_OPEN = os.environ.get("SECURITY_SMOKE_ALLOW_OPEN") == "1"
ALLOW_DOCS = os.environ.get("SECURITY_SMOKE_ALLOW_DOCS") == "1"
TIMEOUT = float(os.environ.get("SECURITY_SMOKE_TIMEOUT", "10"))

# The local UI origin the shipped allowlist carries (tracker #7/#13). Unknown
# origins are refused by design, so the probe uses this one.
UI_ORIGIN = os.environ.get("SECURITY_SMOKE_ORIGIN", "http://localhost:8000")

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (f" — {detail}" if detail else ""))


def request(method, path, headers=None, body=None):
    """Return (status, lowercased-headers, text). Raises URLError if down."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read().decode()
    except urllib.error.HTTPError as ex:
        return ex.code, {k.lower(): v for k, v in ex.headers.items()}, ex.read().decode()


def main():
    print("── security smoke (live API) ──")
    try:
        status, _, body = request("GET", "/api/health")
    except (urllib.error.URLError, OSError) as ex:
        print(f"  [SKIP] API not reachable at {BASE} ({getattr(ex, 'reason', ex)})")
        print("SKIPPED: live security probes need the platform running (scripts/start)")
        return 0

    check("health", status == 200, f"status={status}")

    # 2. Anonymous reads. Session mode gates every /api/* read; flag-off mode
    # leaves reads open by contract, so that posture must be opted into.
    status, _, _ = request("GET", "/api/cases")
    if ALLOW_OPEN and status == 200:
        check("anonymous read refused", True, "200 allowed by SECURITY_SMOKE_ALLOW_OPEN")
    else:
        check("anonymous read refused", status == 401, f"status={status} (want 401)")

    # 3. Anonymous mutations. 422/404 here means the gate let the request
    # through to validation/routing — the exact bypass this probe exists for.
    for path, body in (("/api/execute", {}), ("/api/cases/99999999/delete", {})):
        status, _, _ = request("POST", path, body=body)
        check(f"anonymous mutation refused {path}", status in (401, 403), f"status={status} (want 401/403)")

    # 4. Schema recon surface.
    for path in ("/docs", "/openapi.json"):
        status, _, _ = request("GET", path)
        ok = status == 404 or (ALLOW_DOCS and status == 200)
        check(f"{path} not exposed", ok, f"status={status} (want 404)")

    # 5. CORS preflight: an allowlisted origin, and no wildcard in the answer.
    status, headers, _ = request(
        "OPTIONS",
        "/api/cases",
        headers={
            "Origin": UI_ORIGIN,
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": "X-API-Key",
        },
    )
    methods = headers.get("access-control-allow-methods", "")
    allowed_headers = headers.get("access-control-allow-headers", "")
    origin = headers.get("access-control-allow-origin", "")
    wildcards = [label for label, value in
                 (("methods", methods), ("headers", allowed_headers), ("origin", origin))
                 if value.strip() == "*"]
    check("CORS preflight has no wildcard", not wildcards,
          f"wildcard={wildcards}" if wildcards else f"status={status} origin={origin or '(refused)'}")

    # 6. The session endpoint must not describe an anonymous caller.
    status, _, body = request("GET", "/api/auth/session")
    leaks_user = False
    if status == 200:
        try:
            payload = json.loads(body or "{}")
            leaks_user = bool(payload.get("user") or payload.get("username") or payload.get("role"))
        except ValueError:
            leaks_user = True
    check("anonymous session refused", status == 401 and not leaks_user, f"status={status}")

    failed = [name for name, ok, _ in results if not ok]
    print()
    if failed:
        print(f"FAILED {len(failed)}/{len(results)} — fix the regression, or bypass with: git push --no-verify")
        return 1
    print(f"OK: {len(results)}/{len(results)} security probes held")
    return 0


if __name__ == "__main__":
    sys.exit(main())
