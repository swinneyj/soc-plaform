<#
.SYNOPSIS
    Wipe SOC Platform operational tables and load clean dummy data.

.DESCRIPTION
    1. Wipes triage_results, splunk_events (pasted notables), analysis, evidence, etc.
    2. Runs the project's existing seed_test_cases.py (TEST-* triage rows).
    3. Runs the project's existing seed_supportive_results.py (optional evidence).
    4. Inserts a clean set of historical/closed notables for UI testing.

    Requires:
      - Platform (or at least Postgres) running on localhost:5433
      - Python with the project dependencies available
      - Run from project root, or keep this script inside db_reset_dummy\

.EXAMPLE
    cd C:\Users\justin.swinney\Documents\SOC_Automation_Working_clean
    .\db_reset_dummy\Reset-DummyDb.ps1

.EXAMPLE
    # If you copied the scripts into the project root:
    .\Reset-DummyDb.ps1 -SkipSupportive
#>

param(
    [string]$RepoRoot = "",
    [string]$DatabaseUrl = "",
    [int]$ApiPort = 8000,
    [switch]$SkipSupportive,
    [switch]$SkipVerify
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
$env:PYTHONWARNINGS = "ignore"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host ("=== {0} ===" -f $Message) -ForegroundColor Cyan
}

function Test-ApiReady {
    param([string]$Url)
    try {
        $null = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Resolve project root
# Works whether this script lives in:
#   <project>\Reset-DummyDb.ps1
#   <project>\db_reset_dummy\Reset-DummyDb.ps1
# ---------------------------------------------------------------------------
if (-not $RepoRoot) {
    $scriptDir = $PSScriptRoot
    if (-not $scriptDir) {
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    }

    $candidateParent = Resolve-Path (Join-Path $scriptDir "..") -ErrorAction SilentlyContinue
    if ($candidateParent -and (Test-Path (Join-Path $candidateParent.Path "db\models.py"))) {
        $RepoRoot = $candidateParent.Path
    } elseif (Test-Path (Join-Path $scriptDir "db\models.py")) {
        $RepoRoot = $scriptDir
    } else {
        $RepoRoot = $scriptDir
    }
}

# Resolve DATABASE_URL from .env when not passed explicitly, so
# callers that rely on the Downloads\SOC_Platform\.env password do not fail auth.
if (-not $DatabaseUrl) {
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path $envFile) {
        $envContent = Get-Content $envFile -Raw -ErrorAction SilentlyContinue
        if ($envContent -match '(?m)^DATABASE_URL\s*=\s*(.+?)\s*$') {
            $DatabaseUrl = $Matches[1].Trim().Trim('"').Trim("'")
            Write-Host ("[env] Resolved DATABASE_URL from .env") -ForegroundColor DarkGray
        }
    }
}
# Fallback to env or default host port
if (-not $DatabaseUrl) {
    if ($env:DATABASE_URL) {
        $DatabaseUrl = $env:DATABASE_URL
    }
}
if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
    Write-Host ("[env] Using default DATABASE_URL (no password from .env)") -ForegroundColor Yellow
}
# Also honor API_HOST_PORT from .env for verification step
if (Test-Path (Join-Path $RepoRoot ".env")) {
    $envContent2 = Get-Content (Join-Path $RepoRoot ".env") -Raw -ErrorAction SilentlyContinue
    if ($PSBoundParameters.ContainsKey('ApiPort') -eq $false -and $envContent2 -match '(?m)^API_HOST_PORT\s*=\s*(\d+)') {
        $ApiPort = [int]$Matches[1]
    }
}

Set-Location $RepoRoot
$env:SOC_PLATFORM_ROOT = $RepoRoot
$env:DATABASE_URL = $DatabaseUrl

Write-Host ("Repo root     : {0}" -f $RepoRoot)
Write-Host ("DATABASE_URL  : {0}" -f $DatabaseUrl)
Write-Host ("API port      : {0}" -f $ApiPort)

# Locate scripts (pack folder or same folder as this PS1)
$packDir = $PSScriptRoot
if (-not $packDir) {
    $packDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$wipeScript = Join-Path $packDir "wipe_db.py"
$closedScript = Join-Path $packDir "seed_dummy_closed_notables.py"

# Fallbacks if user flattened the pack into project root
if (-not (Test-Path $wipeScript)) {
    $wipeScript = Join-Path $RepoRoot "wipe_db.py"
}
if (-not (Test-Path $closedScript)) {
    $closedScript = Join-Path $RepoRoot "seed_dummy_closed_notables.py"
}

$testCasesScript = Join-Path $RepoRoot "seed_test_cases.py"
$supportiveScript = Join-Path $RepoRoot "seed_supportive_results.py"

foreach ($required in @($wipeScript, $closedScript)) {
    if (-not (Test-Path $required)) {
        Write-Error ("Missing required file: {0}" -f $required)
        exit 1
    }
}

# ---------------------------------------------------------------------------
# 1. Wipe
# ---------------------------------------------------------------------------
Write-Step "Wiping operational tables"
python $wipeScript
if ($LASTEXITCODE -ne 0) {
    Write-Error ("wipe_db.py failed with exit code {0}" -f $LASTEXITCODE)
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# 2. Triage test cases (project's own seed)
# ---------------------------------------------------------------------------
Write-Step "Seeding TEST-* triage cases"
if (Test-Path $testCasesScript) {
    python $testCasesScript
    if ($LASTEXITCODE -ne 0) {
        Write-Error ("seed_test_cases.py failed with exit code {0}" -f $LASTEXITCODE)
        exit $LASTEXITCODE
    }
} else {
    Write-Warning ("seed_test_cases.py not found at {0} -- skipping" -f $testCasesScript)
}

# ---------------------------------------------------------------------------
# 3. Supportive results (optional)
# ---------------------------------------------------------------------------
if (-not $SkipSupportive) {
    Write-Step "Seeding supportive query results"
    if (Test-Path $supportiveScript) {
        python $supportiveScript
        if ($LASTEXITCODE -ne 0) {
            Write-Error ("seed_supportive_results.py failed with exit code {0}" -f $LASTEXITCODE)
            exit $LASTEXITCODE
        }
    } else {
        Write-Warning "seed_supportive_results.py not found -- skipping"
    }
} else {
    Write-Host "[*] Skipping supportive seed (-SkipSupportive)"
}

# ---------------------------------------------------------------------------
# 4. Closed / historical notables
# ---------------------------------------------------------------------------
Write-Step "Seeding dummy closed notables"
python $closedScript
if ($LASTEXITCODE -ne 0) {
    Write-Error ("seed_dummy_closed_notables.py failed with exit code {0}" -f $LASTEXITCODE)
    exit $LASTEXITCODE
}

# ---------------------------------------------------------------------------
# 5. Verify via API (if up)
# ---------------------------------------------------------------------------
if (-not $SkipVerify) {
    Write-Step "Verifying via API"
    $statsUrl = "http://127.0.0.1:{0}/api/db/stats" -f $ApiPort
    if (Test-ApiReady -Url $statsUrl) {
        $stats = Invoke-RestMethod $statsUrl
        Write-Host ($stats | ConvertTo-Json -Depth 5)

        Write-Host ""
        Write-Host "Triage sample:"
        try {
            $triageUrl = "http://127.0.0.1:{0}/api/db/triage?limit=10" -f $ApiPort
            $triage = Invoke-RestMethod $triageUrl
            $triage | ForEach-Object {
                Write-Host ("  {0}  {1}  {2}" -f $_.case_id, $_.verdict, $_.rule_name)
            }
        } catch {
            Write-Warning ("Could not list triage: {0}" -f $_)
        }

        Write-Host ""
        Write-Host "Closed notables sample:"
        try {
            $histUrl = "http://127.0.0.1:{0}/api/db/notables/historical?limit=20" -f $ApiPort
            $hist = Invoke-RestMethod $histUrl
            $hist | ForEach-Object {
                Write-Host ("  id={0}  host={1}  disposition={2}" -f $_.id, $_.host, $_.disposition)
            }
        } catch {
            Write-Warning ("Could not list historical notables: {0}" -f $_)
        }
    } else {
        Write-Warning ("API not reachable at {0} -- skip live verify. Data is still in the DB." -f $statsUrl)
        Write-Host "Start the platform and re-check with:"
        Write-Host ("  Invoke-RestMethod http://127.0.0.1:{0}/api/db/stats" -f $ApiPort)
    }
}

Write-Host ""
Write-Host "Done. Dummy DB is ready for UI / delete / timeline work." -ForegroundColor Green
Write-Host "Suggested checks:"
Write-Host ("  Invoke-RestMethod http://127.0.0.1:{0}/api/db/stats" -f $ApiPort)
Write-Host ("  Invoke-RestMethod 'http://127.0.0.1:{0}/api/db/triage?limit=50'" -f $ApiPort)
Write-Host ("  Invoke-RestMethod 'http://127.0.0.1:{0}/api/db/notables/historical?limit=20'" -f $ApiPort)
