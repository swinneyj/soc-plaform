# Mock Splunk Backend (Phase 3)

Deterministic, credential-free search backend so Phase 3 (notable →
auto-run supportive queries → evidence ledger) runs entirely on seeded
data — locally, in CI, and on laptops without Splunk access. Real
Splunk REST later plugs in behind the same interface.

## Quick start

```bash
# 1. Seed (idempotent; --reset wipes previous mock rows)
DATABASE_URL='postgresql+psycopg://daltonlewis@localhost:5432/soc_platform' \
  .venv314/bin/python scripts/seed_mock_splunk.py --reset

# 2. Use from Python
from services.search_backend import get_search_backend
backend = get_search_backend()          # SEARCH_BACKEND env, default "mock"
rows = backend.search("sourcetype=linux_secure action=failure | stats count by user", earliest="-7d")
```

`SEARCH_BACKEND=mock` is the default. `SEARCH_BACKEND=splunk` selects the
Phase 3 REST connector (raises `NotImplementedError` until wired — loud
failure by design).

## What's seeded

Three scenarios with known ground truth (in
`scripts/seed_mock_splunk.py::SCENARIOS`):

| Case | Rule | Ground truth | Evidence |
|---|---|---|---|
| `MOCK-CASE-001` | Impossible Travel (high) | suspicious | 6 failed logins + 1 success from a distant geo, user `bjones` |
| `MOCK-CASE-002` | Malware Detection (critical) | malicious | 3 blocked binaries dropped by WINWORD on `HOST-FIN-04` |
| `MOCK-CASE-003` | Data Exfiltration (medium) | benign | cloud-backup burst explains outbound volume on `HOST-DEV-02` |

Each scenario ships 2 supportive SPL queries the mock backend can actually
execute. The seeder runs every query through the backend and persists the
rows into `supportive_query_results` (`source_system='splunk'`), so the
existing analysis/evidence flows see them like any other ingested result.

## Supported SPL subset (honest bounds)

- Base search: `key=value` terms (quotes supported) and free-text terms
- Pipeline: `| search k=v`, `| where k OP v` (`=`, `!=`, `>`, `<`, `>=`, `<=`,
  `AND`), `| stats count (by field)`, `| head N`
- Time: `earliest`/`latest` as `-15m`/`-24h`/`-7d` offsets, ISO timestamps,
  or `all`/`now`
- Anything else raises `ValueError: unsupported SPL operator` — the mock
  never silently pretends.

## Files

- `services/search_backend.py` — `SearchBackend` abstraction,
  `MockSplunkBackend`, `RealSplunkBackend` stub, `get_search_backend()`
- `services/mock_splunk/seed/seed_events.jsonl` — committed event corpus
  (regenerated relative to *now* by the seeder unless `--no-events`)
- `scripts/seed_mock_splunk.py` — seeder (`--reset`, `--no-events`,
  `SEED_ANCHOR` env to pin the clock)
- `tests/test_search_backend.py` — 18 hermetic tests

## Adding a scenario

Add an entry to `SCENARIOS` in `scripts/seed_mock_splunk.py` (rule, case,
verdict, queries + matching event builders), rerun the seeder, and extend
`build_events()` so the queries return their ground-truth rows.
