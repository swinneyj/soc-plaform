param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DatabaseUrl = "",
    [string]$DumpPath = "",
    [string]$SharedDumpPath = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff\current_soc_platform_dump.sql",
    [switch]$SkipDependencyInstall,
    [switch]$SkipPostgresStart,
    [switch]$SkipRestore,
    [switch]$SkipSharedLogicSync,
    [int]$PostgresHostPort = 5433,
    [int]$ApiPort = 8000
)

if (-not $DatabaseUrl) {
    $DatabaseUrl = $env:DATABASE_URL
}
if (-not $DatabaseUrl) {
    if (-not $env:POSTGRES_PASSWORD) {
        throw "POSTGRES_PASSWORD is required. Copy .env.example to .env and set a private password."
    }
    $encodedPassword = [System.Uri]::EscapeDataString($env:POSTGRES_PASSWORD)
    $databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "soc_platform" }
    $databaseName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "soc_platform" }
    $DatabaseUrl = "postgresql+psycopg://$databaseUser`:$encodedPassword@localhost`:$PostgresHostPort/$databaseName"
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

    if (-not $DumpPath -and $SharedDumpPath -and (Test-Path $SharedDumpPath)) {
        Write-Host "[*] No local dump specified. Using shared handoff dump at $SharedDumpPath"
        $localBackupsDir = Join-Path $RepoRoot "local-backups"
        if (-not (Test-Path $localBackupsDir)) {
            New-Item -ItemType Directory -Path $localBackupsDir -Force | Out-Null
        }

        $localDumpPath = Join-Path $localBackupsDir "current_soc_platform_dump.sql"
        Copy-Item $SharedDumpPath $localDumpPath -Force
        $DumpPath = $localDumpPath
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
Write-Host "[*] Launching API on port $ApiPort with DATABASE_URL configured (password hidden)"
python -m uvicorn --app-dir $RepoRoot api.main:app --host 127.0.0.1 --port $ApiPort
