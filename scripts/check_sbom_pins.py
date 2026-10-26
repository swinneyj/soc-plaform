#!/usr/bin/env python3
"""Assert that docs/SBOM.json still describes what the repo actually pins.

Why this exists: `docs/SBOM-AUDIT.md`'s findings only hold while the SBOM
tracks the pins it claims to describe, but nothing checked that relationship —
a Dependabot bump or a CDN tag edit could leave the SBOM describing an older
environment with every other gate green. Regenerating with `scripts/sbom` on
every push would need network + throwaway tooling; this check is the cheap
half: it compares the committed SBOM against the committed pins.

Checks, stdlib only (so CI can run it before installing anything):

1. **Python pins** — every exact pin in `requirements*.txt` selected by the
   *running* interpreter's `python_version` markers must appear in the SBOM at
   the same version. The other branch's pins are skipped: the SBOM is a
   snapshot of one environment, not of both marker branches.
2. **Frontend pins** — `web/index.modular.html` is the only pin for the three
   CDN libraries (no npm lockfile), so its `vue@`, `axios@` and
   `cdn.tailwindcss.com/` tags must match the SBOM's components.

Marker evaluation is imported from `scripts/check_requirement_markers.py`
rather than reimplemented, so the two gates can never disagree about which
branch a pin belongs to.

Exit codes: 0 clean, 1 drift found, 2 usage/parse problem — the same shape as
the other pre-install gates.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENT_FILES = ("requirements.txt", "requirements-dev.txt")
SBOM = "docs/SBOM.json"
HTML = "web/index.modular.html"

PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\[(?P<extras>[^\]]+)\])?"
    r"==(?P<version>[^\s;]+)$"
)
HASH_RE = re.compile(r"--hash[=\s]+\S+")
CDN_RE = {
    "vue": re.compile(r"npm/vue@(?P<version>\d+\.\d+\.\d+)"),
    "axios": re.compile(r"npm/axios@(?P<version>\d+\.\d+\.\d+)"),
    "tailwindcss": re.compile(r"cdn\.tailwindcss\.com/(?P<version>\d+\.\d+\.\d+)"),
}


def _helpers():
    """Load the marker helpers from the sibling gate (single implementation)."""
    spec = importlib.util.spec_from_file_location(
        "_soc_req_markers", ROOT / "scripts" / "check_requirement_markers.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_pins(path, markers, target):
    """Return `[(name, version, where)]` for pins this interpreter selects.

    Hash continuations (`--hash=sha256:...`) are stripped: they are artifact
    integrity data, not pins. Unparseable requirement lines are a loud
    usage error — silently skipping one is how a check goes quiet.
    """
    if not path.is_file():
        raise markers.UsageError("requirements file not found: %s" % path)

    logical = []
    pending = ""

    def flush():
        if pending.strip():
            logical.append(pending.strip())

    for raw in path.read_text().splitlines():
        line = HASH_RE.sub("", raw).strip()
        # A blank line ends a hash-continuation block: the last --hash line
        # ends with a backslash too, so only a blank line can terminate it.
        if not line:
            flush()
            pending = ""
            continue
        if line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1].strip() + " "
            continue
        pending += line
        flush()
        pending = ""
    flush()

    pins = []
    for line in logical:
        if line.startswith("-") or line.startswith("--"):
            continue  # options / -r includes: not pins
        requirement, _, marker = line.partition(";")
        where = "%s: %s" % (path.name, line)
        match = PIN_RE.match(requirement.strip())
        if not match:
            raise markers.UsageError(
                "%s: not an exact pin (this gate only understands `name==version`)" % where
            )
        if marker.strip() and not markers.marker_selects(marker.strip(), target, where):
            continue
        pins.append((match.group("name"), match.group("version"), where))
    return pins


def load_sbom(markers):
    path = ROOT / SBOM
    if not path.is_file():
        raise markers.UsageError("SBOM not found: %s" % SBOM)
    try:
        payload = json.loads(path.read_text())
    except ValueError as exc:
        raise markers.UsageError("%s is not valid JSON: %s" % (SBOM, exc))
    components = payload.get("components") or []
    if not isinstance(components, list) or not components:
        raise markers.UsageError("%s carries no components" % SBOM)
    return {
        markers.normalize_name(str(component.get("name", ""))): str(component.get("version", ""))
        for component in components
        if component.get("name")
    }


def frontend_pins():
    """Return `[(name, version, where)]` from the UI's CDN <script> tags."""
    path = ROOT / HTML
    if not path.is_file():
        return []
    text = path.read_text()
    found = []
    for name, pattern in CDN_RE.items():
        match = pattern.search(text)
        if match:
            found.append((name, match.group("version"), "%s: %s@%s" % (HTML, name, match.group("version"))))
    return found


def main():
    markers = _helpers()
    target = "%d.%d" % sys.version_info[:2]
    print("Checking %s against pins selected for Python %s" % (SBOM, target))

    sbom = load_sbom(markers)
    pins = []
    for name in REQUIREMENT_FILES:
        pins.extend(read_pins(ROOT / name, markers, target))
    frontend = frontend_pins()
    pins.extend(frontend)

    problems = []
    seen = set()
    checked = 0
    for name, version, where in pins:
        key = markers.normalize_name(name)
        if key in seen:
            continue  # the same pin can appear in both files
        seen.add(key)
        checked += 1
        recorded = sbom.get(key)
        if recorded is None:
            problems.append("%-22s missing from %s (pin %s)" % (name, SBOM, version))
        elif recorded != version:
            problems.append(
                "%-22s %s has %s, %s pins %s"
                % (name, SBOM, recorded, where.split(":")[0], version)
            )

    if problems:
        print("SBOM drift (%d):" % len(problems))
        for problem in problems:
            print("  - %s" % problem)
        print("Regenerate with `scripts/sbom` and commit the result.")
        return 1

    print(
        "OK: %d pins (%d Python, %d frontend) match %s"
        % (checked, checked - len(frontend), len(frontend), SBOM)
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
