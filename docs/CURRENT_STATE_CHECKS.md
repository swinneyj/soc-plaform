# Current-state checks (auto-derived, refresh before trusting)

Run these from the repo root on `dev-dalton` to re-derive the numbers the older
docs quote (some of those numbers are stale vs. disk):

```bash
# How many Python test files does the repo actually have?
ls -1 tests/test_*.py | wc -l

# How many UI regression harness files?
ls -1 tests/ui_regression/*.mjs | wc -l

# Full suite summary (both runtimes if you want the matrix):
.venv314/bin/python -m pytest -q   # summary line = "N passed"
.venv/bin/python -m pytest -q      # same N expected
```

The gating scripts are the source of truth for passing/failing; this file is only
a pointer so nobody has to remember the commands.

- Test files: `tests/test_*.py` — currently **12** on disk (older docs quote 8;
  that figure is stale).
- UI regression harnesses: `tests/ui_regression/*.mjs` — currently **3** files
  on disk (`harness_core.mjs` is the shared core; the other two are scenarios).
  Older docs quote "23 scenarios"; that figure is stale — re-derive scenario names
  from the two scenario files plus `harness_core.mjs` before assuming a count.
- Baseline: `bash scripts/baseline --verify` (must be ✅ BASELINE HOLDING before
  any code-touching work; run it before you start and after you finish).
- Undefined-name gate: `bash scripts/check_undefined_names.sh` (falls back to the
  repo venvs if bare `python3` lacks pyflakes — no venv activation needed;
  `PYTHON=<path>` still forces a specific interpreter. Python only; the reason it
  exists is 3.14 lazy annotation masking — see script header).
- Merge-marker gate: `bash scripts/check_no_merge_markers.sh`.
- JS syntax: `for f in $(find web -name '*.js' -not -path 'web/Old_archive/*'); do node --check "$f" || echo "FAIL $f"; done`
- Auth seam tests (API-key + session-auth matrix): `tests/test_api_key_gate.py`.
- Investigation-loop regression tests: `tests/test_investigation_fixes.py`,
  `tests/test_api_analyze_flow.py`, `tests/test_closure_gate.py`,
  `tests/test_phase2_evidence_verdicts.py`, `tests/test_search_backend.py`,
  `tests/test_splunk_boundary.py`.
- Boundary ingest coverage: `tests/test_boundary_ingest_fuzz.py`,
  `tests/test_boundary_ingest_properties.py`, `tests/test_splunk_boundary.py`.
- Security/errors/availability coverage: `tests/test_internal_errors.py`,
  `tests/test_frontend_xss_sinks.py`, `tests/test_report_download.py`.

Never add a new `tests/test_*.py` file without an explicit human decision — the
count is a tracked invariant in the playbook for a reason (it keeps the test
estate countable). Add tests to the existing files instead.
