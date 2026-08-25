param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DatabaseUrl = "",
    [string]$DumpPath = "",
    [switch]$SkipDependencyInstall,
    [switch]$SkipPostgresStart,
    [switch]$SkipRestore,
    [switch]$SkipSharedLogicSync,
    [int]$PostgresHostPort = 5433,
    [int]$ApiPort = 8000
)

if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:$PostgresHostPort/soc_platform"
}

Set-Location $RepoRoot

Write-Host "[*] Repo root: $RepoRoot"

if (-not $SkipDependencyInstall) {
    Write-Host "[*] Installing Python dependencies..."
    python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $SkipPostgresStart) {
    Write-Host "[*] Starting PostgreSQL container..."
    $env:POSTGRES_HOST_PORT = "$PostgresHostPort"
    docker compose up -d postgres
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (-not $SkipRestore) {
    if (-not $DumpPath) {
        $candidate = Join-Path $RepoRoot "local-backups\current_soc_platform_dump.sql"
        if (Test-Path $candidate) {
            $DumpPath = $candidate
        }
    }

    if ($DumpPath -and (Test-Path $DumpPath)) {
        Write-Host "[*] Restoring PostgreSQL dump from $DumpPath"
        & (Join-Path $PSScriptRoot "restore_postgres_dump.ps1") -InputPath $DumpPath -DatabaseUrl $DatabaseUrl
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host "[*] No dump file provided or found. Skipping restore."
    }
}

if (-not $SkipSharedLogicSync) {
    Write-Host "[*] Syncing shared rules and supportive queries from repo..."
    & (Join-Path $PSScriptRoot "sync_shared_logic_to_db.ps1") -RepoRoot $RepoRoot -DatabaseUrl $DatabaseUrl
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$env:DATABASE_URL = $DatabaseUrl
Write-Host "[*] Launching API on port $ApiPort with DATABASE_URL=$DatabaseUrl"
python -m uvicorn --app-dir $RepoRoot api.main:app --host 127.0.0.1 --port $ApiPort