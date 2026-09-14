param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$ApiPort = 8000,
    [int]$PostgresHostPort = 5433,
    [switch]$OpenBrowser,
    [switch]$SkipPlatformStart,
    [switch]$SkipTestCaseSeed,
    [switch]$SkipSupportiveSeed
)

function Test-HttpReady {
    param([string]$Url)
    try {
        $null = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

Set-Location $RepoRoot

$databaseUrl = "postgresql+psycopg://soc_platform@localhost:$PostgresHostPort/soc_platform"
$env:DATABASE_URL = $databaseUrl
$env:SOC_PLATFORM_ROOT = $RepoRoot

Write-Host "============================================"
Write-Host "  Prepare Local DB Repro"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"
Write-Host "[*] DATABASE_URL=$databaseUrl"

if (-not $SkipPlatformStart) {
    if (Test-HttpReady -Url "http://127.0.0.1:$ApiPort/health") {
        Write-Host "[+] API already healthy on port $ApiPort"
    } else {
        Write-Host "[*] Starting platform in local resume mode (no shared dump restore)..."
        & (Join-Path $PSScriptRoot "start_platform.ps1") -RepoRoot $RepoRoot -ApiPort $ApiPort -PostgresHostPort $PostgresHostPort -EnsureOllama -SkipSharedDumpRestore -OpenBrowser:$OpenBrowser
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
}

if (-not $SkipTestCaseSeed) {
    Write-Host "[*] Seeding synthetic triage test cases..."
    python .\seed_test_cases.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not $SkipSupportiveSeed) {
    Write-Host "[*] Seeding synthetic supportive results..."
    python .\seed_supportive_results.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "[+] Local DB repro environment is ready."
Write-Host "[+] Suggested checks:"
Write-Host "    http://127.0.0.1:$ApiPort/api/db/triage"
Write-Host "    http://127.0.0.1:$ApiPort/api/db/stats"
