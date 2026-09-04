param(
    [Parameter(Mandatory = $true)]
    [string]$Branch,
    [string]$RepoRoot = $null,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [int]$BaseApiPort = 9000,
    [int]$BaseDbPort = 9300,
    [int]$BaseRedisPort = 9600,
    [int]$PortRange = 300
)

function Wait-ForPostgres {
    param(
        [string]$ProjectName,
        [int]$MaxAttempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        docker compose -p $ProjectName exec -T postgres pg_isready -U soc_platform -d soc_platform *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

if (-not $RepoRoot) {
    if ($PSScriptRoot) {
        $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    } elseif ($PSCommandPath) {
        $RepoRoot = (Resolve-Path (Join-Path (Split-Path $PSCommandPath -Parent) "..")).Path
    } else {
        $RepoRoot = (Get-Location).Path
    }
}

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Starting branch preview from shared dump"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"
Write-Host "[*] Branch: $Branch"

[int]$hash = [Math]::Abs($Branch.GetHashCode())
[int]$appPort = $BaseApiPort + ($hash % $PortRange)
[int]$dbPort = $BaseDbPort + ($hash % $PortRange)
[int]$redisPort = $BaseRedisPort + ($hash % $PortRange)

Write-Host "[*] Computed ports -> App: $appPort, DB: $dbPort, Redis: $redisPort"

$env:API_HOST_PORT = "$appPort"
$env:POSTGRES_HOST_PORT = "$dbPort"
$env:REDIS_PORT = "$redisPort"
$env:COMPOSE_DATABASE_URL = "postgresql+psycopg2://soc_platform@postgres:5432/soc_platform"
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:$dbPort/soc_platform"

$projectName = "soc-$Branch"

Write-Host "[*] Stopping existing preview project (if any): $projectName"
docker compose -p $projectName down --remove-orphans

Write-Host "[*] Starting postgres and redis for branch preview..."
docker compose -p $projectName up -d postgres redis
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up postgres/redis failed for project $projectName"
    exit $LASTEXITCODE
}

Write-Host "[*] Waiting for PostgreSQL to become ready for project $projectName..."
if (-not (Wait-ForPostgres -ProjectName $projectName -MaxAttempts 60 -DelaySeconds 2)) {
    Write-Error "PostgreSQL did not become ready in time for project $projectName"
    exit 1
}

$dumpPath = Join-Path $SharedDumpDir "current_soc_platform_dump.sql"

if (Test-Path $dumpPath) {
    Write-Host "[*] Restoring shared dump into branch preview DB: $dumpPath"

    Write-Host "[*] Resetting public schema before restore..."
    docker compose -p $projectName exec -T postgres psql -U soc_platform -d soc_platform -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to reset PostgreSQL schema before restore for project $projectName"
        exit $LASTEXITCODE
    }

    Write-Host "[*] Applying dump file to branch preview DB..."
    Get-Content $dumpPath | docker compose -p $projectName exec -T postgres psql -U soc_platform -d soc_platform
    if ($LASTEXITCODE -ne 0) {
        Write-Error "psql restore failed with exit code $LASTEXITCODE for project $projectName"
        exit $LASTEXITCODE
    }

    Write-Host "[+] Restore complete for branch preview DB"
} else {
    Write-Warning "Shared dump file not found: $dumpPath. Starting branch preview with empty DB."
}

Write-Host "[*] Starting api-service for branch preview via docker compose..."
docker compose -p $projectName up -d --build --force-recreate api-service
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up api-service failed for project $projectName"
    exit $LASTEXITCODE
}

Write-Host "[+] Branch preview environment is ready!"
Write-Host "    URL: http://localhost:$appPort/"
Write-Host "    DB Port: $dbPort"
