#!/usr/bin/env python3
"""Validate that every requirement pin is installable on the interpreter its
python_version marker selects, using PyPI's requires_python metadata.

Why this exists: commit 8358281 pinned hypothesis==6.149.1 to the
`python_version < "3.10"` branch of requirements-dev.txt, but 6.149.1
requires Python >=3.10 — the CI 3.9 leg died inside pip's install step with
an opaque resolver error. This check asks PyPI what each pinned version
actually supports and fails fast with a pointed message when the interpreter
a marker selects is outside that range.

Scope: exact pins (`name==version`, optional extras) in the dual-runtime
requirement files, each optionally followed by a `; marker` clause. Markers
may use python_version comparisons joined by `and`/`or`; anything else
(unknown variables, other marker fields, parentheses) fails loudly rather
than being silently skipped.

Targets default to the CI matrix interpreters (keep in sync with
.github/workflows/tests.yml); override with repeated --python flags.

Stdlib only — runs under both matrix interpreters (3.9 and 3.14) before any
dependency install. Exit codes: 0 ok, 1 pin/marker mismatch, 2 usage,
parse, or PyPI lookup failure.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_TARGETS = ["3.9", "3.14"]  # keep in sync with the tests.yml matrix
DEFAULT_FILES = ["requirements.txt", "requirements-dev.txt"]

PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]+)\])?"
    r"==(?P<version>[^\s;]+)$"
)
CLAUSE_RE = re.compile(
    r'^(?P<var>python_version)\s*(?P<op>==|!=|<=|>=|<|>)\s*["\'](?P<val>[^"\']+)["\']\s*$'
)
SPEC_RE = re.compile(r"^(?P<op>===|==|!=|>=|<=|>|<)\s*(?P<ver>\S+)$")

UA = "soc-plaform-requirement-markers/1.0"


class UsageError(Exception):
    """Unparseable requirement line or marker — exit 2 (loud, never skip)."""


def version_tuple(text, context):
    try:
        parts = tuple(int(p) for p in text.split("."))
    except ValueError:
        raise UsageError("cannot parse version %r in %s" % (text, context))
    if not parts:
        raise UsageError("empty version in %s" % context)
    return parts


def compare(op, left, right):
    """PEP 440-style numeric tuple compare; 3.9 < 3.10 (never string order)."""
    width = max(len(left), len(right))
    left = left + (0,) * (width - len(left))
    right = right + (0,) * (width - len(right))
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    raise UsageError("unsupported comparison operator %r" % op)


def marker_selects(marker, target, where):
    """Evaluate a PEP 508 python_version-only marker for a target interpreter."""
    if not marker:
        return True
    if "(" in marker or ")" in marker:
        raise UsageError("%s: parenthesized markers are not supported" % where)
    # `or` binds looser than `and`.
    for alternative in marker.split(" or "):
        if not all(
            eval_clause(clause.strip(), target, where)
            for clause in alternative.split(" and ")
        ):
            return False
    return True


def eval_clause(clause, target, where):
    match = CLAUSE_RE.match(clause)
    if not match:
        raise UsageError(
            "%s: unsupported marker clause %r (only python_version "
            "comparisons joined by and/or are supported)" % (where, clause)
        )
    return compare(
        match.group("op"),
        version_tuple(target, "target interpreter"),
        version_tuple(match.group("val"), "marker clause %r" % clause),
    )


def requires_python_allows(spec, target, where):
    """True if every specifier clause admits the target interpreter version."""
    if not spec:
        return True  # PyPI metadata absent: no declared restriction
    base = version_tuple(target, "target interpreter")
    # A 2-segment target ("3.9") means "a current interpreter of that minor":
    # treat the patch as the newest of the series, mirroring CI's setup-python
    # (it installs the latest patch). Without this, patch-specific clauses
    # like cryptography's `!=3.9.0,!=3.9.1,>=3.9` would false-fail a target
    # of exactly X.Y.0. Minor-level constraints (`>=3.10`, `<3.10`) still
    # decide the check — that is the mismatch class this gate exists for.
    target_t = base + (999,) if len(base) == 2 else base
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            continue
        match = SPEC_RE.match(clause)
        if not match:
            raise UsageError(
                "%s: unsupported requires_python clause %r from PyPI" % (where, clause)
            )
        op = match.group("op")
        if op == "===":
            if match.group("ver") != target:
                return False
            continue
        if not compare(op, target_t, version_tuple(match.group("ver"), "PyPI requires_python")):
            return False
    return True


def normalize_name(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def fetch_requires_python(name, version, where, cache):
    key = (normalize_name(name), version)
    if key in cache:
        return cache[key]
    url = "https://pypi.org/pypi/%s/%s/json" % key
    last_error = None
    for attempt in range(2):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.load(response)
            spec = payload["info"].get("requires_python")
            cache[key] = spec
            return spec
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UsageError(
                    "%s: %s==%s does not exist on PyPI (typo in the pin?)" % (where, name, version)
                )
            last_error = "HTTP %d" % exc.code
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = str(exc)
        if attempt == 0:
            time.sleep(1)
    raise UsageError("%s: could not query PyPI for %s==%s (%s)" % (where, name, version, last_error))


def parse_line(raw, where):
    line = re.split(r"\s+#", raw.strip(), maxsplit=1)[0].strip()
    if not line or line.startswith("#"):
        return None
    req, sep, marker = line.partition(";")
    if sep and not marker.strip():
        raise UsageError("%s: empty marker after ';'" % where)
    match = PIN_RE.match(req.strip())
    if not match:
        raise UsageError(
            "%s: unsupported requirement line %r (only exact pins "
            "`name==version` with an optional `; python_version marker` "
            "are validated)" % (where, line)
        )
    return match.group("name"), match.group("version"), marker.strip()


def check_file(path, targets, cache, failures):
    pins = 0
    checks = 0
    for lineno, raw in enumerate(path.read_text().splitlines(), 1):
        where = "%s:%d" % (path.name, lineno)
        parsed = parse_line(raw, where)
        if parsed is None:
            continue
        name, version, marker = parsed
        pins += 1
        for target in targets:
            if not marker_selects(marker, target, where):
                continue
            checks += 1
            spec = fetch_requires_python(name, version, where, cache)
            if not requires_python_allows(spec, target, where):
                failures.append(
                    "%s: %s==%s selected for Python %s (marker: %s) but PyPI "
                    "requires_python is %s"
                    % (where, name, version, target, marker or "<none>", spec)
                )
    return pins, checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--python",
        dest="targets",
        action="append",
        metavar="VERSION",
        help="target interpreter to validate against (repeatable; "
        "default: %s)" % ", ".join(DEFAULT_TARGETS),
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="requirement files to check (default: %s)" % ", ".join(DEFAULT_FILES),
    )
    args = parser.parse_args(argv)
    targets = args.targets or list(DEFAULT_TARGETS)
    root = Path(__file__).resolve().parent.parent
    paths = []
    for name in args.files or DEFAULT_FILES:
        candidate = Path(name)
        if not candidate.is_file():
            candidate = root / name
        if not candidate.is_file():
            parser.error("requirement file not found: %s" % name)
        paths.append(candidate)

    failures = []
    cache = {}
    total_pins = total_checks = 0
    try:
        for path in paths:
            pins, checks = check_file(path, targets, cache, failures)
            total_pins += pins
            total_checks += checks
    except UsageError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    if failures:
        for failure in failures:
            print("FAIL: %s" % failure, file=sys.stderr)
        print(
            "\n%d pin/marker mismatch(es) across %d pins (%d checks against "
            "Python %s). A marker branch is selecting a pin that does not "
            "support that interpreter — fix the pin or the marker."
            % (len(failures), total_pins, total_checks, ", ".join(targets)),
            file=sys.stderr,
        )
        return 1
    print(
        "OK: %d pins, %d checks — every marker-selected pin supports its "
        "interpreter (targets: %s; PyPI requires_python)"
        % (total_pins, total_checks, ", ".join(targets))
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
