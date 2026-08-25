# Setup Guide

## Purpose

This guide is the single setup path for a new user who pulls the project from Git and needs both code and current database-backed runtime behavior.

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

1. Clone the repo.
2. Obtain `current_soc_platform_dump.sql` from the maintainer.
3. Run:

```powershell
.\scripts\bootstrap_new_user.ps1 -DumpPath .\local-backups\current_soc_platform_dump.sql
```

That script will:
1. install Python dependencies
2. start the PostgreSQL container
3. restore the dump if present
4. sync shared logic from the repo into the database
5. launch the API

If multiple clones must run on the same machine, choose another PostgreSQL host port:

```powershell
.\scripts\bootstrap_new_user.ps1 -DumpPath .\local-backups\current_soc_platform_dump.sql -PostgresHostPort 5434 -ApiPort 8006
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
python -m uvicorn --app-dir C:\Users\%USERNAME%\Downloads\SOC_Automation_Working api.main:app --host 127.0.0.1 --port 8000
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