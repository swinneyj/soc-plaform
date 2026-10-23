#!/usr/bin/env python3
"""Emit the route/role matrix (SEC-02 inventory) from the live FastAPI app.

The matrix in docs/ROUTE_ROLE_MATRIX.md must describe the app as it is, not as
someone remembered it, so it is generated: every mounted route, its methods,
the guards it already carries (API-key dependency, role dependency and which
role), whether the mutation gate covers it, and the role the SEC-02 proposal
assigns. Regenerating after a route change is one command:

    python scripts/route_matrix.py            # markdown table on stdout
    python scripts/route_matrix.py --check    # exit 1 if the doc is stale

``--check`` compares the generated table against the fenced block in the doc,
so a new endpoint cannot silently ship outside the row-count the matrix claims.

Proposed roles (the owner still has to approve the policy — this is a proposal,
not enforcement):

* **admin** — machine/ops surfaces: the tool catalog and execution, job
  control, the Splunk boundary, and anything destructive (delete/delete-all/
  batch-delete/wipe). These are the nine routes that already carry a role gate.
* **analyst** — the investigation flow: cases, notables, evidence, analyses,
  closure, rules/playbooks, reports, read-only Splunk search, system reads.
* **public** — health and the auth bootstrap, which must answer before login.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DOC = os.path.join(ROOT, "docs", "ROUTE_ROLE_MATRIX.md")
BEGIN = "<!-- BEGIN GENERATED ROUTE MATRIX -->"
END = "<!-- END GENERATED ROUTE MATRIX -->"

PUBLIC_PATHS = ("/health", "/api/health", "/api/auth/login", "/api/auth/session")
ADMIN_PREFIXES = ("/api/tools", "/api/jobs", "/api/splunk-boundary")
DESTRUCTIVE_MARKERS = ("delete", "wipe", "purge", "execute")


def _dependency_names(dependant, seen=None):
    """(names, roles) for every dependency under a route's Dependant tree.

    Role dependencies are closures over the role string, so the role is read
    out of the closure cell. That is how `Depends(require_role("admin"))`
    becomes visible here without importing every router's local alias.
    """
    names, roles = set(), set()
    stack = [dependant]
    while stack:
        node = stack.pop()
        call = getattr(node, "call", None)
        if call is not None:
            qualname = getattr(call, "__qualname__", "")
            names.add(getattr(call, "__name__", "") or qualname)
            if "require_role" in qualname:
                for cell in getattr(call, "__closure__", None) or ():
                    try:
                        value = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(value, str):
                        roles.add(value)
            stack.extend(getattr(node, "dependencies", []) or [])
    return names, roles


ROUTER_MODULES = (
    "analyze", "closure", "code_review", "evidence", "notables", "promote",
    "rules", "splunk", "system", "tools", "triage", "auth", "ui",
)


def _route_objects():
    """(path, methods, dependant) for every mounted API route.

    Read from each router module rather than ``app.routes``: FastAPI 0.146
    stores included routers as ``_IncludedRouter`` wrappers with no ``path``
    or ``methods``, so walking the app would report zero routes. The routers'
    own ``routes`` lists are plain ``APIRoute`` objects — and their paths are
    absolute, so no prefix mapping is needed. ``verify_coverage`` then proves
    the set matches what the app actually serves.
    """
    import importlib

    found = []
    for name in ROUTER_MODULES:
        module = importlib.import_module("api.routes.%s" % name)
        router = getattr(module, "router", None)
        for route in getattr(router, "routes", []) or []:
            path = getattr(route, "path", "")
            if not (path.startswith("/api") or path == "/health"):
                continue
            methods = sorted(
                m for m in (getattr(route, "methods", None) or ())
                if m not in ("HEAD", "OPTIONS")
            )
            if methods:
                found.append((path, methods, getattr(route, "dependant", None)))
    return found


def verify_coverage(found):
    """Count method+path pairs against the app's OpenAPI schema.

    A route added to a module outside ROUTER_MODULES, or declared without
    landing in a router, would show up here as a mismatch instead of quietly
    missing from the matrix.
    """
    from api.main import app

    # OpenAPI rewrites Starlette path converters (`{artifact_path:path}`) to
    # plain `{artifact_path}`, so both sides are normalized before diffing —
    # otherwise one route reports as simultaneously missing and extra.
    def normalize(path):
        return re.sub(r"\{([^}:]+):[^}]+\}", r"{\1}", path)

    served = {
        (normalize(path), method.upper())
        for path, operations in app.openapi().get("paths", {}).items()
        for method in operations
    }
    listed = {(normalize(path), method) for path, methods, _d in found for method in methods}
    return len(served), len(listed), sorted(served - listed) + sorted(listed - served)


def collect(found=None):
    from api import auth

    rows = []
    for path, methods, dependant in (found if found is not None else _route_objects()):
        names, roles = _dependency_names(dependant)
        keyed = any(n in ("require_api_key",) for n in names)
        role = next(iter(sorted(roles)), "")
        mutating = bool(
            set(methods) & {"POST", "PUT", "PATCH", "DELETE"}
            or any(path.endswith(s) for s in ("/delete", "/batch-delete", "/delete-all"))
        )
        if path in PUBLIC_PATHS:
            proposed = "public"
        elif (
            path.startswith(ADMIN_PREFIXES)
            or role == "admin"
            or any(marker in path for marker in DESTRUCTIVE_MARKERS)
        ):
            proposed = "admin"
        else:
            proposed = "analyst"
        rows.append(
            {
                "methods": ",".join(methods),
                "path": path,
                "gated": "session/proxy" if path not in auth.EXEMPT_PATHS else "exempt",
                "mutating": "yes" if mutating else "no",
                "key": "yes" if keyed else "-",
                "role": role or "-",
                "proposed": proposed,
            }
        )
    rows.sort(key=lambda r: (r["path"], r["methods"]))
    return rows


def render(rows) -> str:
    counts = {}
    for row in rows:
        counts[row["proposed"]] = counts.get(row["proposed"], 0) + 1
    lines = [
        "| # | Method | Path | Mutating | Existing key dep | Existing role dep | Proposed role |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        lines.append(
            "| %d | `%s` | `%s` | %s | %s | %s | **%s** |"
            % (
                index,
                row["methods"],
                row["path"],
                row["mutating"],
                row["key"],
                row["role"],
                row["proposed"],
            )
        )
    lines.append("")
    lines.append(
        "**%d routes** — %d proposed **admin**, %d **analyst**, %d **public**."
        % (
            len(rows),
            counts.get("admin", 0),
            counts.get("analyst", 0),
            counts.get("public", 0),
        )
    )
    return "\n".join(lines)


def coverage_line() -> str:
    served, listed, diff = verify_coverage(_route_objects())
    verdict = "match" if not diff else "MISMATCH: %s" % diff
    return (
        "*Completeness check:* `app.openapi()` serves **%d** method+path pairs, "
        "this matrix lists **%d** — %s." % (served, listed, verdict)
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="emit the SEC-02 route/role matrix")
    parser.add_argument("--check", action="store_true", help="verify the doc is current")
    args = parser.parse_args(argv)

    table = render(collect()) + "\n\n" + coverage_line()
    if args.check:
        try:
            doc = open(DOC, encoding="utf-8").read()
        except FileNotFoundError:
            print("missing %s" % os.path.relpath(DOC, ROOT))
            return 1
        block = doc.split(BEGIN, 1)[-1].split(END, 1)[0].strip()
        if block != table.strip():
            print("docs/ROUTE_ROLE_MATRIX.md is stale — regenerate with scripts/route_matrix.py")
            return 1
        summary = next(line for line in table.splitlines() if line.startswith("**"))
        print("route matrix current (%s)" % summary.replace("**", ""))
        return 0
    print(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
