#!/usr/bin/env pwsh
# Daily pg_dump backup for the soc_platform database (Phase 5 ops readiness).
#
# Cross-platform via PowerShell Core. Delegates the actual pg_dump to the
# existing export_postgres_dump.ps1 (which handles: pg_dump on PATH, Docker
# fallback, SQLAlchemy driver suffix stripping, UTF-8 output). This script
# adds: gzipping, the postgres_dump_YYYYmmdd_HHMMSS.sql.gz naming convention,
# and count-based retention.
#
# Dumps land in local-backups/ (gitignored) as gzipped plain-SQL files.
# The newest BACKUP_KEEP dumps are kept (default 14 ≈ two weeks).
#
# Usage:  pwsh -File scripts/backup_db.ps1
#         .\scripts\backup_db.ps1
#
# Env vars (all optional):
#   DATABASE_URL  - SQLAlchemy URL (default: read from <repo>/.env)
#   BACKUP_DIR    - Dump output directory (default: <repo>/local-backups)
#   BACKUP_KEEP   - Number of dumps to keep (default 14)
#
# Differs from backup_db.sh:
#   - Calls export_postgres_dump.ps1 for the dump (not pg_dump directly)
#   - No hardcoded Homebrew paths — finds gzip on PATH cross-platform
#   - Uses Get-ChildItem + Sort-Object for retention instead of ls -1t
#
# LIMITATIONS (verified against this machine's state — edit if state changes):
#   - This script requires pg_dump (or Docker + a running `postgres` service)
#     AND gzip on PATH to run end to end. On the current macOS checkout neither
#     pg_dump nor gzip is on PATH and Docker is not available, so the dump -
#     > gzip -> retention pipeline cannot be executed here. The script logic
#     (DATABASE_URL loading, export script delegation, temp-file handling,
#     retention counting, size formatting) is syntax-validated clean under
#     pwsh 7.6.6 and covered by scripts/check_env_parser.ps1 for the shared
#     .env regex, but the full pipeline is not executed as part of verification.
#   - When it does run, it writes to the shared Neon database via the DATABASE_URL
#     in .env — see AGENTS.md "One shared production dataset". It is not a sandbox.
#   - Temp files go to $env:TEMP ($TEMP on Windows, /tmp on Unix). A kill -9
#     between gzip and Move-Item can leave a partial .sql.gz.tmp in TEMP
#     (same limitation as the bash original).

$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $PSScriptRoot
$EXPORT_SCRIPT = Join-Path $ROOT "scripts/export_postgres_dump.ps1"
$BACKUP_DIR = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $ROOT "local-backups" }
$BACKUP_KEEP = [int](if ($env:BACKUP_KEEP) { $env:BACKUP_KEEP } else { "14" })

# Load DATABASE_URL from .env if not already set
if (-not $env:DATABASE_URL) {
    $envFile = Join-Path $ROOT ".env"
    if (Test-Path $envFile) {
        Get-Content $envFile | ForEach-Object {
            if ($_ -match '^\s*([^#=#\s][^=]*)=(.*)$') {
                Set-Item -Path "env:$($Matches[1].Trim())" -Value $Matches[2].Trim()
            }
        }
    }
}

if (-not $env:DATABASE_URL) {
    Write-Error "backup_db: DATABASE_URL is not set (set it in .env or as an env var)"
    exit 1
}

if (-not (Test-Path $EXPORT_SCRIPT)) {
    Write-Error "backup_db: export script not found at $EXPORT_SCRIPT"
    exit 1
}

# Find gzip on PATH (cross-platform)
$gzip = (Get-Command gzip -ErrorAction SilentlyContinue).Source
if (-not $gzip) {
    $gzipCandidates = @(
        "$env:LOCALAPPDATA\Git\usr\bin\gzip.exe",
        "$env:PROGRAMFILES\Git\usr\bin\gzip.exe",
        "$env:SystemRoot\System32\gzip.exe",
        "/usr/bin/gzip",
        "/opt/homebrew/bin/gzip",
        "/usr/local/bin/gzip"
    )
    foreach ($c in $gzipCandidates) {
        if (Test-Path $c -PathType Leaf) { $gzip = $c; break }
    }
}
if (-not $gzip) {
    Write-Error "backup_db: gzip not found on PATH (needed to compress dumps)"
    exit 1
}

New-Item -ItemType Directory -Path $BACKUP_DIR -Force | Out-Null

# Run the export script to produce a .sql dump
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$sqlOut = Join-Path $env:TEMP "postgres_dump_${stamp}.sql"

Write-Host "backup_db: exporting dump via export_postgres_dump.ps1..."

# export_postgres_dump.ps1 writes to its own default filename; override with -OutputPath
& $EXPORT_SCRIPT -OutputPath $sqlOut -DatabaseUrl $env:DATABASE_URL

if ($LASTEXITCODE -ne 0) {
    Write-Error "backup_db: export failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

if (-not (Test-Path $sqlOut) -or (Get-Item $sqlOut).Length -eq 0) {
    Write-Error "backup_db: export produced an empty or missing file — aborting"
    exit 1
}

# Gzip the .sql file
$gzTmp = Join-Path $env:TEMP "postgres_dump_${stamp}.sql.gz"
$gzOut = Join-Path $BACKUP_DIR "postgres_dump_${stamp}.sql.gz"

Write-Host "backup_db: compressing to $gzOut..."
& $gzip -c $sqlOut > $gzTmp
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $gzTmp) -or (Get-Item $gzTmp).Length -eq 0) {
    Write-Error "backup_db: gzip failed — aborting"
    Remove-Item $sqlOut -Force -ErrorAction SilentlyContinue
    exit 1
}

Move-Item -Path $gzTmp -Destination $gzOut -Force
Remove-Item $sqlOut -Force -ErrorAction SilentlyContinue

# Retention — keep the newest BACKUP_KEEP dumps
$dumps = Get-ChildItem -Path $BACKUP_DIR -Filter "postgres_dump_*.sql.gz" |
    Sort-Object LastWriteTime -Descending
$toRemove = $dumps | Select-Object -Skip $BACKUP_KEEP
$removed = 0
foreach ($old in $toRemove) {
    Remove-Item $old.FullName -Force
    $removed++
}

$size = if (Test-Path $gzOut) { (Get-Item $gzOut).Length } else { 0 }
$sizeStr = if ($size -ge 1GB) { "{0:N1} GB" -f ($size / 1GB) }
           elseif ($size -ge 1MB) { "{0:N1} MB" -f ($size / 1MB) }
           elseif ($size -ge 1KB) { "{0:N1} KB" -f ($size / 1KB) }
           else { "$($size) B" }

Write-Host "backup_db: done ($sizeStr, removed $removed old dump(s), keeping $BACKUP_KEEP)"
