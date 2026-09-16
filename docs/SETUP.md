# Setup Guide

## Purpose

This guide is the single setup path for a new user who pulls the project from Git and needs both code and current database-backed runtime behavior.

At a high level:
- Git shares the codebase and shared logic.
- Each user runs the app locally.
- Current operational data is handed off by PostgreSQL dump instead of a live shared database.

## What Comes From Git

Git provides:
- source code
- configuration
- migration scripts
- documentation

Git does not provide:
- current live PostgreSQL data
- the preserved SQLite backup file
- user-specific `.env` values

## Recommended Runtime Model

Use the project-managed PostgreSQL container on `localhost:5433`.

Reason:
- the repo is already configured for it
- it avoids conflicts with any separate PostgreSQL instance on `localhost:5432`
- it gives consistent behavior across users

## Fastest New-User Path

### Environment Checklist (per machine)

Before running any scripts, each analyst should have:

- Docker Desktop installed and running (containers must be able to start).
- Ollama installed and running (`ollama serve`), with at least `llama3.1:latest` pulled. The platform auto-resolves the model at request time — set `OLLAMA_MODEL` in `.env` to pin a specific tag.
- Reachable shared PostgreSQL dump at
	`Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff\current_soc_platform_dump.sql`.
- A local `.env` file in the repo root (copy `.env.example` to `.env`) with:
	- `POSTGRES_PASSWORD` set to a private, user-specific password.
	- Optional `DATABASE_URL` and `COMPOSE_DATABASE_URL` entries using the same password, or let the scripts compute them.

No passwords or dumps are stored in Git; this local setup is intentional.

1. Clone the repo.
2. Make sure the shared handoff dump is reachable at:
	`Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff\current_soc_platform_dump.sql`
3. Run:

```powershell
.\scripts\bootstrap_new_user.ps1
```

That script will:
1. install Python dependencies
2. start the PostgreSQL container
3. restore the local dump if present, otherwise pull the shared handoff dump from `Z:` automatically
4. sync shared logic from the repo into the database
5. launch the API

If multiple clones must run on the same machine, choose another PostgreSQL host port:

```powershell
.\scripts\bootstrap_new_user.ps1 -PostgresHostPort 5434 -ApiPort 8006
```

## Manual Path

### Start PostgreSQL

```powershell
docker compose up -d postgres
```

### Restore current data

```powershell
.\scripts\restore_postgres_dump.ps1 -InputPath .\local-backups\current_soc_platform_dump.sql
```

This helper resets the `public` schema before import so rerunning setup on the same machine does not accumulate duplicate objects.

### Start the API

```powershell
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
python -m uvicorn --app-dir . api.main:app --host 127.0.0.1 --port 8000
```

## Creating a Current Dump for Another User

```powershell
.\scripts\export_postgres_dump.ps1 -OutputPath .\local-backups\current_soc_platform_dump.sql
```

Share that dump out of band.

## SQLite Backup Path

SQLite is not the default runtime anymore.

It is retained only for backup/export and recovery scenarios. If you are given the SQLite file instead of a PostgreSQL dump, use:

```powershell
python .\scripts\migrate_sqlite_to_postgres.py --target-url "postgresql+psycopg://soc_platform@localhost:5433/soc_platform" --drop-existing
```

## Health Checks

Both routes are supported:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## After A Reboot

Use this from the repo root:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

Or double-click `Start_SOC_Platform.bat`.

Commander is optional. If you want it, run `python .\commander.py` separately.

## If A User Wants To Use Their Installed PostgreSQL Instead

They can, but it should be an explicit choice.

Example:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/soc_platform"
```

That is outside the default project path and should only be used if the team decides to adopt that installed PostgreSQL service as the maintained runtime.

## Shared Logic Refresh After Pulling Git Changes

When a user pulls rule or supportive-query changes from Git, refresh their local PostgreSQL copy of the shared logic with:

```powershell
.\scripts\sync_shared_logic_to_db.ps1
```

## Branch Preview Environments (Feature Branches)

To test changes on a feature branch without touching the main/staging runtime, the repo uses branch-specific preview environments:

- The Git pre-push hook at [.git/hooks/pre-push](.git/hooks/pre-push) creates a Docker Compose project named `soc-<branch>` for any branch that is not `main` or `staging`.
- Ports for the preview are assigned dynamically based on the branch name (stable per branch):
	- app port: 9000 + (hash(branch) % 300)
	- PostgreSQL host port: 9300 + (hash(branch) % 300)
	- Redis port: 9600 + (hash(branch) % 300)
- The preview uses its own PostgreSQL volume (isolated from the main/staging volume) so test data does not contaminate the primary runtime.

To keep preview DBs aligned with the current shared snapshot, the hook calls [scripts/start_branch_preview_from_shared_dump.ps1](scripts/start_branch_preview_from_shared_dump.ps1):

- Stops any existing preview project for that branch (`docker compose -p soc-<branch> down --remove-orphans`).
- Starts PostgreSQL and Redis for the branch (`docker compose -p soc-<branch> up -d postgres redis`) and waits for PostgreSQL health.
- If `Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff\current_soc_platform_dump.sql` exists, it resets the `public` schema in the branch DB and restores that shared dump.
- Starts the branch API service via Docker Compose (`docker compose -p soc-<branch> up -d --build --force-recreate api-service`).

Result: each push from a feature branch both rebuilds and reseeds its sandbox from the shared handoff dump, while keeping its data isolated from the main/staging runtime.