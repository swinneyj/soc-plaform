# SOC Platform Repo Map

## Purpose

This file answers the quickest navigation question first:

Where is the thing I need?

Use this as the fastest orientation guide for the clean working copy.

---

## Start Here

If you are new to this repo, read in this order:

1. `README.txt`
2. `RUNBOOK.md`
3. `OPERATOR_CHEAT_SHEET.md`
4. `docs\FLOW_SHEETS.md`
5. `docs\SYSTEM_VISUAL_MAP.md`
6. `docs\GIT_VISUAL_MAP.md`
7. `docs\API_DB_VISUAL_MAP.md`
8. `docs\NEW_DEVELOPER_ONBOARDING.md`
9. `docs\REPO_MAP.md`
10. `docs\SETUP.md`
11. `docs\SHARED_LOGIC_MODEL.md`

---

## Main Areas

### Runtime and App Surface

- `api\`
  FastAPI service layer
- `db\`
  SQLAlchemy models and DB wiring
- `services\`
  External service clients such as Ollama
- `web\`
  Browser dashboard UI

### Operational Scripts

- `scripts\start_platform.ps1`
  Start dockerized runtime and wait for API health
- `scripts\status_platform.ps1`
  Check Docker/API/Ollama/PostgreSQL status
- `scripts\stop_platform.ps1`
  Stop runtime services and export the shared dump when enabled
- `scripts\bootstrap_new_user.ps1`
  Install deps, start PostgreSQL, restore dump, sync shared logic, and launch the API
- `scripts\restore_postgres_dump.ps1`
  Restore PostgreSQL dump into the active runtime DB
- `scripts\sync_shared_logic_to_db.ps1`
  Re-import repo-managed rules and supportive queries into PostgreSQL
- `scripts\sync_upstream_safe.ps1`
  Safe pull helper for this working copy

### Orchestration and Tools

- `commander.py`
  Main local CLI orchestrator
- `Commander_Registry.json`
  Tool registry used by Commander and API
- `Tools\tool_indexer\tool_indexer.py`
  Rebuilds `Commander_Registry.json`
- `Tools\core_lib\`
  Shared utility code used across Commander and tools
- `Tools\`
  Individual SOC tools, grouped by function

### Data and Local State

- `local-backups\`
  Local dump staging area copied from the shared handoff folder during bootstrap
- `Data\`
  Runtime support folders, generated content, and reports

### Documentation

- `README.txt`
  Quick start and operator-facing overview
- `RUNBOOK.md`
  Runtime/database model and onboarding flow
- `docs\`
  Focused operational references
- `docs\REPO_MAP.md`
  This navigation guide

---

## Docs Folder

Use `docs\` like this:

- `docs\SETUP.md`
  Setup instructions
- `docs\RESTART.md`
  Restart flow and wrapper behavior
- `docs\FLOW_SHEETS.md`
  Beginner-friendly operator decision flows
- `docs\SYSTEM_VISUAL_MAP.md`
  Visual architecture and mental-model map
- `docs\GIT_VISUAL_MAP.md`
  Visual Git, sync, and branch mental model
- `docs\API_DB_VISUAL_MAP.md`
  Visual API, database, and route mental model
- `docs\NEW_DEVELOPER_ONBOARDING.md`
  Short guided path for a new developer
- `docs\DAILY_WORKFLOW.md`
  Daily operating routine
- `docs\SHARED_LOGIC_MODEL.md`
  Rules/supportive-query ownership model
- `docs\REPO_MAP.md`
  File and folder navigation map

---

## Common Tasks

### Pull the latest changes safely

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\sync_upstream_safe.ps1 -CheckOnly
```

### Pull with an automatic checkpoint

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

### Start the platform

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\start_platform.ps1 -OpenBrowser
```

### Check platform health

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\status_platform.ps1
```

### Stop the platform cleanly

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\stop_platform.ps1
```

---

## Practical Rule

If you cannot quickly find something, look in this order:

1. `scripts\` for runnable operational workflows
2. `docs\` for operator instructions
3. `README.txt` and `RUNBOOK.md` for environment and handoff expectations
4. `api\`, `db\`, `services\`, `web\` for app/runtime code
5. `Tools\` for analyst and workflow tooling