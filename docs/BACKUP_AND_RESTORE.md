# Backups & Restore — `soc_platform`

*Phase 5 ops readiness. Companion to `docs/DEVELOPMENT_PLAN.md` §7.*

## Schedule

**There is no scheduler running today** — no backup LaunchAgent is loaded on this
Mac (`launchctl list | grep soc` is empty; verified 2026-10-23), and that is the
owners' decision: boot persistence is deliberately off (`AGENTS.md`).

- **Rides along with platform starts:** every `scripts/start` refreshes the dump
  when the newest one is more than 20h old (`fresh (7h old) — skipping` in the
  banner). **Manual / forced:** `scripts/start --backup`.
- **Standalone run:** `set -a; . ./.env; set +a; bash scripts/backup_db.sh`
  (needs `DATABASE_URL` + `pg_dump` on PATH).
- **Optional escape hatch:** `scripts/local.soc-platform.backup.plist` (daily
  03:30) exists in the repo but is **not installed**; installing it is
  `cp` + `launchctl load` if a scheduler is ever wanted.
- **Requirement:** `pg_dump` must be at least the server's major version. Neon runs PostgreSQL 18, so an older client (e.g. Homebrew `postgresql@16`) refuses with a version-mismatch error — install `libpq` (`brew install libpq`) or a matching `postgresql@N` and make sure its `pg_dump` is first on PATH.
- The Windows side keeps its manual equivalents: `scripts/export_postgres_dump.ps1` / `scripts/restore_postgres_dump.ps1`.

## What gets written

- Dumps land in **`local-backups/`** (gitignored) as `postgres_dump_YYYYmmdd_HHMMSS.sql.gz` — gzipped plain SQL, `pg_dump --no-owner --no-privileges`.
- **Retention:** the newest `BACKUP_KEEP` dumps are kept (default 14 ≈ two weeks). Override with `BACKUP_KEEP=30 bash scripts/backup_db.sh`.
- `DATABASE_URL` is read from the environment only — never committed, never in the dump filenames. With the launchers it is always set for you (local Postgres by default; `--shared` dumps the Neon mirror instead — see `docs/LOCAL_FIRST_DATABASE.md`), so **run the backup in the same mode as the platform you mean to protect**.

## Restore

1. **Stop writers.** Restart-free restores risk the API overwriting restored rows; stop the API first (`scripts/restart_api.sh` or `launchctl unload` the API plist).
2. **Restore the dump** into the target database:

   ```bash
   gunzip -c local-backups/postgres_dump_YYYYmmdd_HHMMSS.sql.gz | psql "$DATABASE_URL"
   ```

   Plain-SQL dumps restore with `psql` (not `pg_restore`). For a full replacement, recreate the schema first or restore into a fresh database and repoint `DATABASE_URL`.
3. **Verify:**

   ```bash
   psql "$DATABASE_URL" -c "select count(*) from analysis_results;"
   psql "$DATABASE_URL" -c "select count(*) from triage_results;"
   ```

   Compare against the same counts captured before the restore (or against expectations from the UI's Database tab).
4. **Restart the API** and smoke `/api/health`.

## Related maintenance

- **Retention pruning** (keep last N analyses per case; closure-linked cases untouched):

  ```bash
  .venv314/bin/python scripts/prune_analysis_results.py           # dry run
  .venv314/bin/python scripts/prune_analysis_results.py --apply   # delete
  ```

## Notes

- The shared **Neon** database is the system of record: these dumps are point-in-time insurance for the same dataset the Vercel prod and Justin's instance use. Restoring overwrites everyone — coordinate before restoring.
- A dump contains real case data (case ids, analysis text, notable fields). Treat `local-backups/` as sensitive even though it is gitignored.
