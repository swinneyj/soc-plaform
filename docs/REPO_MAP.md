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

### UI Feature Map (Where Each Tab Lives) — modular layout (post-Piece 6c)

Live entry is now a thin shell: `web/index.html` (≈30 KB) → `web/app.modular.js` + `web/components/*` + `web/modules/*` + `web/utils/*`. Use this to jump straight to the owning file:

- **Tools Tab**
  - UI: `web/components/ToolsTab.js` (+ `HeaderNav.js`); shell: `web/index.html`
  - Logic: `web/modules/tools.js` (execute, poll jobs, registry) + `web/modules/api.js`
  - API: `/api/tools`, `/api/execute`, `/api/jobs` in `api/main.py` (+ `api/routes/system.py`)

- **Database Tab (Triage / Notables / Stats)**
  - UI: `web/components/DatabaseTab.js`
  - Logic: `web/modules/database.js` (+ `web/utils/pocSummary.js`, `selection.js`)
  - API: `/api/db/triage`, `/api/db/notables`, `/api/db/stats` in `api/main.py`
  - Models: `TriageResult`, `SplunkEvent` in `db/models.py`

- **AI Analysis Tab**
  - UI: `web/components/AnalysisTab.js`
  - Logic: `web/modules/analysis.js` (phase1/phase2/supportive/aliases/evidence) + `web/utils/queryRender.js`, `keys.js`
  - API: `/api/db/analyze`, `/api/db/triage/{id}/evidence`, `/api/db/placeholder-aliases`, `/api/db/supportive-queries` in `api/main.py`
  - Models: `AnalysisResult`, `SupportiveQuery`, `SupportiveQueryResult`, `InvestigationState` in `db/models.py`
  - Ollama: `services/ollama_service.py` + `services/investigation_state.py` / `evidence_service.py`

- **Closure Notes Tab**
  - UI: `web/components/ClosureTab.js`
  - Logic: `web/modules/closure.js`
  - API: `/api/db/closure-note`, `/api/db/rules` in `api/main.py`
  - Models: `ClosureNote`, `ESCorrelationRule` in `db/models.py`; `services/closure_service.py`

- **Jobs Tab**
  - UI: `web/components/JobsTab.js`
  - Logic: `web/modules/tools.js`
  - API: `/api/jobs` in `api/main.py`

- **Reports Tab**
  - UI: `web/components/ReportsTab.js`
  - Logic: `web/modules/tools.js`
  - API: `/api/reports` in `api/main.py`
  - Files: `Data/Reports/`

- **Code Review Tab**
  - UI: `web/components/CodeReviewTab.js`
  - Logic: `web/modules/codeReview.js` + `web/utils/codeSections.js`
  - API: `/api/code-review`, `/api/code-review/zip`, `/api/code-reviews/*` in `api/main.py`
  - Models: `CodeReview` in `db/models.py`

Root wiring: `web/app.modular.js` (data, computed, spreads, mounted, intervals — see `docs/FRONTEND_MODULARIZATION.md` load order). When you want to modify a feature, start from the tab above and open that component + its module before touching `api/`.

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
# from the repo root (folder containing docker-compose.yml)
.\scripts\sync_upstream_safe.ps1 -CheckOnly
```

### Pull with an automatic checkpoint

```powershell
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

### Start the platform

```powershell
.\scripts\start_platform.ps1 -OpenBrowser
```

### Check platform health

```powershell
.\scripts\status_platform.ps1
```

### Stop the platform cleanly

```powershell
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