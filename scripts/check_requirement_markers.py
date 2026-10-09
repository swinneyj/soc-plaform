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
than being silently skipped. The files are hash-locked (every pin carries
pip's `--hash=sha256:` continuations, see scripts/pin_requirements.py):
hash options are artifact-integrity data, not pins or markers — their shape
is validated, they are stripped, and the pin/marker check then runs on the
requirement itself; a malformed --hash token is a loud usage error.

Targets are derived from the `python-version` matrix in
.github/workflows/tests.yml (override with repeated --python flags), so
the gate validates exactly the interpreters CI runs and the two cannot
drift apart. A missing or unparseable matrix is a loud exit 2 — never a
silent fallback to a hardcoded copy.

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

DEFAULT_FILES = ["requirements.txt", "requirements-dev.txt"]
WORKFLOW = ".github/workflows/tests.yml"
MATRIX_KEY_RE = re.compile(r'^(\s*)python-version:\s*(.*?)\s*$')
LIST_ITEM_RE = re.compile(r'^(\s*)-\s+(.*)$')
TARGET_RE = re.compile(r'^\d+(\.\d+){1,2}$')

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


def strip_comment(text):
    return re.split(r"\s+#", text, maxsplit=1)[0].strip()


def validate_target(value, context):
    if not TARGET_RE.match(value):
        raise UsageError(
            "%s: target %r must be an explicit numeric interpreter version "
            "like 3.9 or 3.14 — not '3.x', 'min', or an implementation alias"
            % (context, value)
        )
    return value


def derive_targets(path):
    """Read every list-valued `python-version:` matrix entry from the workflow.

    Occurrences whose value is an expression
    (`python-version: ${{ matrix.python-version }}`) are references, not
    definitions, and are skipped. Multiple matrix lists are unioned; no list
    at all is a loud UsageError (exit 2), never a hardcoded default.
    """
    if not path.is_file():
        raise UsageError("workflow file not found: %s" % path)
    targets = []
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        match = MATRIX_KEY_RE.match(line)
        if not match:
            continue
        indent, rest = match.group(1), strip_comment(match.group(2))
        items = []
        if rest.startswith("["):
            if not rest.endswith("]"):
                raise UsageError(
                    "%s:%d: multi-line flow list for python-version is not "
                    "supported — use a single-line list or block items"
                    % (path, i + 1)
                )
            items = [item.strip() for item in rest[1:-1].split(",") if item.strip()]
        elif not rest:
            for j in range(i + 1, len(lines)):
                candidate = lines[j]
                stripped = candidate.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                item = LIST_ITEM_RE.match(candidate)
                if item and len(item.group(1)) > len(indent):
                    items.append(strip_comment(item.group(2)).strip())
                    continue
                break
        else:
            continue  # expression reference, not a matrix definition
        for item in items:
            value = validate_target(item.strip("\"'"), "%s:%d" % (path, i + 1))
            if value not in targets:
                targets.append(value)
    if not targets:
        raise UsageError(
            "no python-version matrix list found in %s — the gate cannot "
            "derive its targets; fix the workflow or pass --python" % path
        )
    return targets


def parse_line(raw, where):
    line = re.split(r"\s+#", raw.strip(), maxsplit=1)[0].strip()
    if not line or line.startswith("#"):
        return None
    # Hash-locked line: pip auto-enables --require-hashes as soon as any
    # requirement carries a --hash, so pins are emitted as
    # `name==ver ; marker \` + indented `--hash=sha256:<hex>` lines.
    # Validate the hash shapes (loud on malformed), strip them plus any
    # line-continuation backslash, then check the pin/marker below —
    # integrity data must not blind the interpreter-coverage check.
    if "--hash=" in line:
        for token in re.findall(r"--hash=\S+", line):
            if not re.fullmatch(r"--hash=sha256:[0-9a-fA-F]{64}", token.rstrip("\\")):
                raise UsageError(
                    "%s: malformed hash option %r (expected "
                    "--hash=sha256:<64 hex chars>)" % (where, token)
                )
        line = re.sub(r"\s*--hash=\S+", "", line).strip()
    line = line.rstrip("\\").strip()
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
        help="target interpreter to validate against (repeatable; default: "
        "derived from the python-version matrix in --workflow)",
    )
    parser.add_argument(
        "--workflow",
        default=WORKFLOW,
        metavar="PATH",
        help="workflow to derive default targets from (default: %s)"
        % WORKFLOW,
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="requirement files to check (default: %s)" % ", ".join(DEFAULT_FILES),
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    try:
        if args.targets:
            targets = [validate_target(value, "--python") for value in args.targets]
        else:
            workflow = Path(args.workflow)
            if not workflow.is_file():
                workflow = root / args.workflow
            targets = derive_targets(workflow)
    except UsageError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
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
