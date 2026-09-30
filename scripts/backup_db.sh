#!/bin/bash
# Daily pg_dump backup for the soc_platform database (Phase 5 ops readiness).
#
# Scheduled by scripts/local.soc-platform.backup.plist (launchd, 03:30 daily).
# Dumps land in local-backups/ (gitignored) as gzipped plain-SQL files named
# postgres_dump_YYYYmmdd_HHMMSS.sql.gz, matching the PowerShell export
# convention. The newest BACKUP_KEEP dumps are kept (default 14 ≈ two weeks).
#
# Restore: see docs/BACKUP_AND_RESTORE.md (gunzip -c ... | psql "$DATABASE_URL").
set -euo pipefail

# Prefer the libpq keg's client tools (keg-only, not on the default PATH) and
# make sure Homebrew is reachable under launchd's minimal environment. The
# client must be >= the server's major version — Neon runs PostgreSQL 18.
PATH="/opt/homebrew/opt/libpq/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
export PATH

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/local-backups}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "backup_db: DATABASE_URL is not set (source .env / bws run first)" >&2
    exit 1
fi

if ! command -v pg_dump >/dev/null 2>&1; then
    echo "backup_db: pg_dump not found on PATH (install PostgreSQL client tools)" >&2
    exit 1
fi

# SQLAlchemy URLs carry a driver suffix (postgresql+psycopg://) that pg_dump
# does not understand — strip it down to the plain scheme.
DSN="$(printf '%s' "$DATABASE_URL" | sed -E 's#^(postgres(ql)?)(\+[^:]+):#\1:#')"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/postgres_dump_${STAMP}.sql.gz"
TMP="$OUT.tmp"

# Never leave a partial dump behind: build the .gz under a temp name and
# move it into place only when pg_dump succeeded and produced bytes.
cleanup_partial() { rm -f "$TMP"; }
trap cleanup_partial EXIT

echo "backup_db: dumping to $OUT"
if ! pg_dump --no-owner --no-privileges "$DSN" | gzip > "$TMP"; then
    echo "backup_db: pg_dump failed (hint: pg_dump must be >= the server's major" >&2
    echo "version — check 'show server_version'; e.g. brew install libpq)." >&2
    exit 1
fi

if [ ! -s "$TMP" ]; then
    echo "backup_db: dump is empty — failing" >&2
    exit 1
fi
mv "$TMP" "$OUT"
trap - EXIT

# Count-based retention: keep the newest BACKUP_KEEP dumps.
REMOVED=0
while IFS= read -r old; do
    rm -f "$old"
    REMOVED=$((REMOVED + 1))
done < <(ls -1t "$BACKUP_DIR"/postgres_dump_*.sql.gz 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)))

echo "backup_db: done ($(du -h "$OUT" | cut -f1 | tr -d ' '), removed $REMOVED old dump(s), keeping $BACKUP_KEEP)"
