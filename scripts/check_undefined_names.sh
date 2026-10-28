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

# Interpreter: CI provides `python3` with dev deps installed; locally a fresh
# shell has a bare python3 without pyflakes, so fall back to the repo venvs —
# the gate must pass without venv activation. PYTHON=<path> still forces a
# specific interpreter (and fails loudly if it lacks pyflakes).
if [ -n "${PYTHON:-}" ]; then
    if ! "$PYTHON" -c "import pyflakes" 2>/dev/null; then
        echo "FAIL: pyflakes not available to $PYTHON — pip install -r requirements-dev.txt"
        exit 1
    fi
else
    for cand in python3 .venv314/bin/python .venv/bin/python; do
        if "$cand" -c "import pyflakes" 2>/dev/null; then
            PYTHON="$cand"
            break
        fi
    done
    if [ -z "${PYTHON:-}" ]; then
        echo "FAIL: no interpreter with pyflakes found (tried: python3, .venv314/bin/python, .venv/bin/python)"
        echo "      fix: pip install -r requirements-dev.txt   (or set PYTHON=<interpreter>)"
        exit 1
    fi
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
