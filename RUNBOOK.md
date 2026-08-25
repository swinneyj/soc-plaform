# SOC Automation Runbook

## What Git Contains

This repository contains:
- application source code
- configuration
- migration utilities
- documentation

This repository does not contain:
- the live project database contents
- the preserved local SQLite backup file
- user-specific environment settings

## What A New User Does

1. Clone the repo.
2. Install Python dependencies.
3. Choose a PostgreSQL runtime:
   - use the project container on `localhost:5433`
   - or use an already installed PostgreSQL service on `localhost:5432`
4. Obtain current data separately from Git:
   - PostgreSQL dump
   - or preserved SQLite backup file
5. Restore/import that data into PostgreSQL.
6. Set `DATABASE_URL` and run the API.

## Recommended Team Model

Use PostgreSQL as the runtime database.

Git should remain code-only.

For current data, distribute one of these outside Git:
1. a PostgreSQL dump
2. the preserved SQLite backup file

The repo includes helper scripts for PostgreSQL dump export and restore:
1. `scripts\export_postgres_dump.ps1`
2. `scripts\restore_postgres_dump.ps1`
3. `scripts\bootstrap_new_user.ps1`

## Option A: Use The Project PostgreSQL Container

Start the container:

```powershell
docker compose up -d postgres
```

Use this runtime URL:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
```

One-command setup:

```powershell
.\scripts\bootstrap_new_user.ps1 -DumpPath .\local-backups\current_soc_platform_dump.sql
```

If multiple clones need to run on the same machine, override the PostgreSQL host port:

```powershell
.\scripts\bootstrap_new_user.ps1 -DumpPath .\local-backups\current_soc_platform_dump.sql -PostgresHostPort 5434 -ApiPort 8006
```

## Option B: Use An Installed Local PostgreSQL Service

If the user already has PostgreSQL installed and wants to use `localhost:5432`, create or restore a database there and set:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://<user>:<password>@localhost:5432/soc_platform"
```

Use this only if the team intentionally wants the installed local PostgreSQL service to be the project runtime.

## How To Get Current Data

### Restore a PostgreSQL dump

```powershell
.\scripts\restore_postgres_dump.ps1 -InputPath .\current_soc_platform_dump.sql
```

### Create a PostgreSQL dump for another user

```powershell
.\scripts\export_postgres_dump.ps1 -OutputPath .\current_soc_platform_dump.sql
```

### Migrate from the preserved SQLite backup

If the maintainer provides `splunk-es-backup-toolkit\triage.db`, place it in the working repo and run:

```powershell
python .\scripts\migrate_sqlite_to_postgres.py --target-url "postgresql+psycopg://soc_platform@localhost:5433/soc_platform" --drop-existing
```

## Run Commands

### Run against PostgreSQL

```powershell
python -m uvicorn --app-dir C:\Users\%USERNAME%\Downloads\SOC_Automation_Working api.main:app --host 127.0.0.1 --port 8000
```

### Run temporarily against SQLite backup

```powershell
$env:DATABASE_URL = "sqlite:///C:/Users/%USERNAME%/Downloads/SOC_Automation_Working/splunk-es-backup-toolkit/triage.db"
python -m uvicorn --app-dir C:\Users\%USERNAME%\Downloads\SOC_Automation_Working api.main:app --host 127.0.0.1 --port 8001
```

## Health Checks

Both routes are valid:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

## Recommendation On The Existing 5432 PostgreSQL Service

This machine already has a separate `postgres` process listening on `5432`.

Recommendation:
1. Leave it alone unless you intentionally want to adopt it for this project.
2. Use the repo-managed PostgreSQL container on `5433` by default.
3. Only standardize on `5432` if the team agrees to use the installed PostgreSQL service as the maintained runtime.