# SOC Platform - Current Operating Summary

## Baseline

The project now runs as one clean Git-based workflow:

- a shared bare repository on the handoff share acts as the common Git remote
- each user works from a normal local clone in their own Downloads or working folder
- shared rules, supportive SPL logic, templates, and application code move through Git
- live operational data moves separately through an exported PostgreSQL dump on the share

This replaces the noisier duplicate-folder and release-copy approach and avoids trying to keep one live shared database running directly from the share.

## Runtime Model

The supported default runtime is local PostgreSQL.

- each user starts their own local stack
- the repo-managed PostgreSQL container binds to `localhost:5433` by default
- SQLite is retained only for preservation, migration, backup, and recovery workflows
- database handoff happens through export and restore scripts, not by pointing everyone at one shared live database

## Operational Flow

### New user or new machine

1. Clone the repo from the shared bare Git remote.
2. Run `.\scripts\bootstrap_new_user.ps1` from the repo root.
3. Restore the latest shared dump if one is available.
4. Sync repo-managed shared logic into the local database.
5. Start the API and UI locally.

### Daily use

1. Pull the latest Git changes.
2. Run `.\scripts\sync_shared_logic_to_db.ps1` after rule or logic updates.
3. Start the platform with `.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser`.
4. Work locally against the local PostgreSQL runtime.

### Handoff

1. Export the current local database state with `.\scripts\export_db_dump_to_share.ps1`.
2. Stop the platform with `.\scripts\stop_platform.ps1`.
3. The next user restores the shared dump with `.\scripts\restore_db_dump_from_share.ps1` or through bootstrap.

## Script Coverage

The repo now includes a usable baseline for:

- migration from preserved SQLite into PostgreSQL
- export and restore of PostgreSQL dumps
- bootstrap of a new user or new machine
- start, stop, and status flows for the local platform
- repo-to-database sync for shared logic after Git pulls
- lock-aware use of the shared handoff dump location

## Cleanup Results

The active codebase was reduced to a maintainable working baseline:

- hardcoded user-specific local paths were removed from the supported workflow
- normal startup no longer depends on Commander auto-start
- unused compose pieces were removed from the normal path
- health checks were corrected
- docs were rewritten around clone, bootstrap, start, handoff, and takeover

## Key Principle

Git is for code and shared logic.

Each user runs the app locally.

The database is preserved and handed off through a dump on the share.
