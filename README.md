# Dummy DB Reset Pack

Wipe the SOC Platform operational tables and load a clean, deterministic set of:

- **TEST-*** triage cases (from the project's own `seed_test_cases.py`)
- Matching supportive query results (from `seed_supportive_results.py`)
- **5 clean closed / historical notables** (for Closed Notables view, delete, and details testing)

## Requirements

- Project root is the extracted `SOC_Automation_Working_clean` folder
- Postgres is running (Docker Compose on host port **5433** is the default)
- Python can import `db.models` (same environment you use for the platform)
- Optional: API up on port 8000 for the final verify step

## Install / layout

Copy the entire `db_reset_dummy` folder into your project root so it sits next to `seed_test_cases.py` and `api/`:

```
SOC_Automation_Working_clean/
  seed_test_cases.py
  seed_supportive_results.py
  api/
  db/
  db_reset_dummy/          <--- this pack
    Reset-DummyDb.ps1
    wipe_db.py
    seed_dummy_closed_notables.py
    README.md
```

## One-command reset

From the **project root**:

```powershell
cd C:\Users\justin.swinney\Documents\SOC_Automation_Working_clean
.\db_reset_dummy\Reset-DummyDb.ps1
```

What it does:

1. `wipe_db.py` – truncates operational tables (triage, pasted notables, analysis, evidence, investigation state, closure notes)
2. `python seed_test_cases.py` – loads the 7 TEST-* triage rows
3. `python seed_supportive_results.py` – loads supportive evidence for those cases
4. `seed_dummy_closed_notables.py` – inserts 5 clean historical notables
5. Hits `/api/db/stats`, `/api/db/triage`, and `/api/db/notables/historical` if the API is up

### Useful switches

```powershell
# Skip supportive evidence seed
.\db_reset_dummy\Reset-DummyDb.ps1 -SkipSupportive

# Skip the live API verify (useful if only Postgres is up)
.\db_reset_dummy\Reset-DummyDb.ps1 -SkipVerify

# Custom ports / URL
.\db_reset_dummy\Reset-DummyDb.ps1 -ApiPort 8000 -DatabaseUrl "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
```

## Manual / individual scripts

```powershell
$env:SOC_PLATFORM_ROOT = (Get-Location).Path
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"

python .\db_reset_dummy\wipe_db.py
python .\seed_test_cases.py
python .\seed_supportive_results.py
python .\db_reset_dummy\seed_dummy_closed_notables.py
```

## Verify

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/db/stats
Invoke-RestMethod "http://127.0.0.1:8000/api/db/triage?limit=50"
Invoke-RestMethod "http://127.0.0.1:8000/api/db/notables/historical?limit=20"
```

Expected after a clean run (approximate):

| Metric                         | Count |
|--------------------------------|-------|
| triage_cases                   | 7     |
| pasted_notables_total          | 5     |
| pasted_notables_historical     | 5     |
| pasted_notables_open           | 0     |
| analyses                       | 0     |

Closed notables have clean `host` / `title` / `disposition` values (no truncated parse garbage) so delete and details UI testing stays predictable.

## Notes

- Wipe does **not** drop schema, ES correlation rules, or placeholder aliases.
- Closed notables are inserted directly into `splunk_events` with `historical: true` so they appear in the Closed Notables list without going through the paste sanitizer pipeline.
- Re-running the pack is safe: wipe first, then re-seed.
- If the API is not running, the data is still written to Postgres; start the platform later and refresh the Database tab.
