#!/usr/bin/env bash
# Fail on undefined names (pyflakes F821) in shipped code and tests.
#
# Why this gate exists: Python 3.14 evaluates annotations lazily (PEP 649),
# so a missing typing import in a signature is masked locally — it only blew
# up on the 3.9 CI leg (the api/routes/promote.py `Optional` bug). An
# undefined name is a runtime NameError waiting to happen; everything else
# pyflakes reports (unused imports/locals) is advisory and not gated here.
#
# Scope: api/ db/ services/ tests/ the root entrypoints, scripts/ (top level),
# and the shared tool libs. scripts/attic/ is retired fragments kept for
# history and is deliberately excluded.
set -u
cd "$(dirname "$0")/.."

# Interpreter: CI provides `python`; locally prefer PYTHON=<venv>/bin/python.
PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -c "import pyflakes" 2>/dev/null; then
    echo "FAIL: pyflakes not available to $PYTHON — pip install -r requirements-dev.txt"
    exit 1
fi

FULL=$("$PYTHON" -m pyflakes \
    api/ db/ services/ tests/ \
    seed_test_cases.py commander.py \
    scripts/*.py \
    Tools/core_lib/ Tools/tool_indexer/ \
    2>&1 || true)

# A crashed pyflakes must not look like a clean run.
if echo "$FULL" | grep -q "Traceback (most recent call last)"; then
    echo "$FULL"
    echo "FAIL: pyflakes crashed"
    exit 1
fi

OUT=$(echo "$FULL" | grep "undefined name" || true)

if [ -n "$OUT" ]; then
    echo "$OUT"
    echo "FAIL: undefined names found (would be runtime NameErrors; 3.14 lazy annotations can mask them locally)"
    exit 1
fi
echo "OK: no undefined names (scripts/attic excluded)"
