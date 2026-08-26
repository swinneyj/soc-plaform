param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$ApiPort = 8000,
    [int]$PostgresHostPort = 5433,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [switch]$AsJson,
    [switch]$ShowEvidence,
    [switch]$RunSmokeTest
)

Set-StrictMode -Version Latest

$results = New-Object System.Collections.Generic.List[object]

function Add-CheckResult {
    param(
        [string]$Name,
        [ValidateSet('PASS', 'WARN', 'FAIL')]
        [string]$Status,
        [string]$Issue,
        [string]$ExpectedSolution,
        [string]$Evidence,
        [string]$RecommendedCommand = ''
    )

    $results.Add([pscustomobject]@{
        Check = $Name
        Status = $Status
        Issue = $Issue
        ExpectedSolution = $ExpectedSolution
        Evidence = $Evidence
        RecommendedCommand = $RecommendedCommand
    }) | Out-Null
}

function Get-StartCommand {
    return ".\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser"
}

function Get-TroubleshootCommand {
    return ".\scripts\troubleshoot_platform.ps1"
}

function Write-NextActions {
    param([object[]]$CheckResults)

    $actionMap = [ordered]@{}
    foreach ($result in $CheckResults) {
        if ([string]::IsNullOrWhiteSpace($result.RecommendedCommand)) {
            continue
        }

        if (-not $actionMap.Contains($result.RecommendedCommand)) {
            $actionMap[$result.RecommendedCommand] = $result.Issue
        }
    }

    if ($actionMap.Count -eq 0) {
        Write-Host ''
        Write-Host 'Recommended Next Actions:'
        Write-Host '1. No scripted remediation is required from this report.'
        return
    }

    Write-Host ''
    Write-Host 'Recommended Next Actions:'
    $index = 1
    foreach ($entry in $actionMap.GetEnumerator()) {
        Write-Host "$index. $($entry.Value)"
        Write-Host "   Command: $($entry.Key)"
        $index++
    }
}

function Test-HttpJson {
    param([string]$Url)
    try {
        $body = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return @{ Ok = $true; Body = $body; Error = $null }
    } catch {
        return @{ Ok = $false; Body = $null; Error = $_.Exception.Message }
    }
}

function Test-WebRequest {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -UseBasicParsing
        return @{ Ok = $true; Body = $response; Error = $null }
    } catch {
        return @{ Ok = $false; Body = $null; Error = $_.Exception.Message }
    }
}

function Get-ListeningProcessName {
    param([int]$Port)

    try {
        $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop | Select-Object -First 1
        if (-not $connection) {
            return $null
        }

        $process = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
        if ($process) {
            return "$($process.ProcessName) (PID $($process.Id))"
        }

        return "PID $($connection.OwningProcess)"
    } catch {
        return $null
    }
}

function Add-SmokeResultFromHttpJson {
    param(
        [string]$Name,
        [string]$Url,
        [scriptblock]$Validator,
        [string]$FailureIssue,
        [string]$ExpectedSolution
    )

    $response = Test-HttpJson -Url $Url
    if (-not $response.Ok) {
        Add-CheckResult -Name $Name -Status 'FAIL' -Issue $FailureIssue -ExpectedSolution $ExpectedSolution -Evidence $response.Error -RecommendedCommand (Get-TroubleshootCommand)
        return
    }

    $isValid = $true
    if ($Validator) {
        $isValid = & $Validator $response.Body
    }

    if ($isValid) {
        Add-CheckResult -Name $Name -Status 'PASS' -Issue "$Name succeeded." -ExpectedSolution 'None.' -Evidence (ConvertTo-Json $response.Body -Compress -Depth 4)
    } else {
        Add-CheckResult -Name $Name -Status 'FAIL' -Issue $FailureIssue -ExpectedSolution $ExpectedSolution -Evidence (ConvertTo-Json $response.Body -Compress -Depth 4) -RecommendedCommand (Get-TroubleshootCommand)
    }
}

function Invoke-SmokeTests {
    param([string]$BaseUrl)

    $prereqFailures = @($results | Where-Object Status -eq 'FAIL').Count
    if ($prereqFailures -gt 0) {
        Add-CheckResult -Name 'Smoke test gate' -Status 'WARN' -Issue 'Smoke tests were skipped because hard prerequisite failures are still present.' -ExpectedSolution 'Resolve the hard failures reported above, then rerun the troubleshooter with -RunSmokeTest.' -Evidence "FAIL count before smoke test: $prereqFailures" -RecommendedCommand (Get-TroubleshootCommand)
        return
    }

    $rootResponse = Test-WebRequest -Url "$BaseUrl/"
    if ($rootResponse.Ok -and $rootResponse.Body.Content.Length -gt 0) {
        Add-CheckResult -Name 'Smoke web root' -Status 'PASS' -Issue 'Web root responded with page content.' -ExpectedSolution 'None.' -Evidence ("ContentLength=" + $rootResponse.Body.Content.Length)
    } else {
        Add-CheckResult -Name 'Smoke web root' -Status 'FAIL' -Issue 'Web root did not return page content.' -ExpectedSolution 'Inspect the API static-file mount and web assets, then rerun the smoke test.' -Evidence $rootResponse.Error -RecommendedCommand (Get-TroubleshootCommand)
    }

    Add-SmokeResultFromHttpJson -Name 'Smoke health' -Url "$BaseUrl/health" -Validator { param($body) $body.status -eq 'healthy' } -FailureIssue 'Health endpoint did not report healthy status.' -ExpectedSolution 'Inspect api-service logs and restart the platform before rerunning the smoke test.'
    Add-SmokeResultFromHttpJson -Name 'Smoke api health' -Url "$BaseUrl/api/health" -Validator { param($body) $body.status -eq 'healthy' } -FailureIssue 'Compatibility health endpoint did not report healthy status.' -ExpectedSolution 'Inspect API route compatibility and restart the platform before rerunning the smoke test.'
    Add-SmokeResultFromHttpJson -Name 'Smoke db stats' -Url "$BaseUrl/api/db/stats" -Validator { param($body) ($body.PSObject.Properties.Name -contains 'triage_cases') -and ($body.PSObject.Properties.Name -contains 'splunk_events') -and ($body.PSObject.Properties.Name -contains 'analyses') } -FailureIssue 'Database stats endpoint did not return the expected keys.' -ExpectedSolution 'Verify the database connection and API db stats handler, then rerun the smoke test.'
    Add-SmokeResultFromHttpJson -Name 'Smoke triage list' -Url "$BaseUrl/api/db/triage?limit=1" -Validator { param($body) $body -is [System.Array] -or $body -is [System.Collections.IEnumerable] } -FailureIssue 'Triage list endpoint did not return a list response.' -ExpectedSolution 'Inspect the triage list route and database query path, then rerun the smoke test.'
    Add-SmokeResultFromHttpJson -Name 'Smoke notables list' -Url "$BaseUrl/api/db/notables?limit=1" -Validator { param($body) $body -is [System.Array] -or $body -is [System.Collections.IEnumerable] } -FailureIssue 'Notables list endpoint did not return a list response.' -ExpectedSolution 'Inspect the notables list route and stored pasted notable query path, then rerun the smoke test.'
}

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  SOC Platform Troubleshooting"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"
Write-Host "[*] API port: $ApiPort"
Write-Host "[*] PostgreSQL host port: $PostgresHostPort"

$gitAvailable = $true
git rev-parse --show-toplevel *> $null
if ($LASTEXITCODE -ne 0) {
    $gitAvailable = $false
    Add-CheckResult -Name 'Git repository' -Status 'FAIL' -Issue 'Current folder is not a readable Git repository.' -ExpectedSolution 'Run this script from the repo root or fix Git access first.' -Evidence $RepoRoot -RecommendedCommand 'Set-Location <repo-root>'
}

if ($gitAvailable) {
    $mergeHeadPath = Join-Path $RepoRoot '.git\MERGE_HEAD'
    if (Test-Path $mergeHeadPath) {
        Add-CheckResult -Name 'Git merge state' -Status 'FAIL' -Issue 'A merge is still in progress.' -ExpectedSolution 'Resolve conflicted files, run git add on each resolved file, then git commit to finish the merge.' -Evidence 'MERGE_HEAD present' -RecommendedCommand 'git status; git add <resolved-files>; git commit'
    } else {
        Add-CheckResult -Name 'Git merge state' -Status 'PASS' -Issue 'No merge is in progress.' -ExpectedSolution 'None.' -Evidence 'MERGE_HEAD not present'
    }

    $conflictedFiles = @(git diff --name-only --diff-filter=U)
    if ($conflictedFiles.Count -gt 0) {
        Add-CheckResult -Name 'Git conflicts' -Status 'FAIL' -Issue 'Conflicted files are still unresolved.' -ExpectedSolution 'Open each conflicted file, remove merge markers, git add the file, then complete the merge commit.' -Evidence ($conflictedFiles -join ', ') -RecommendedCommand 'git diff --name-only --diff-filter=U'
    } else {
        Add-CheckResult -Name 'Git conflicts' -Status 'PASS' -Issue 'No unresolved Git conflicts.' -ExpectedSolution 'None.' -Evidence 'No unmerged paths'
    }

    $statusLines = @(git status --short)
    if ($statusLines.Count -gt 0) {
        Add-CheckResult -Name 'Git working tree' -Status 'WARN' -Issue 'Local uncommitted changes are present.' -ExpectedSolution 'Commit, stash, or discard local changes before risky pulls or before declaring a clean baseline for smoke testing.' -Evidence (($statusLines | Select-Object -First 6) -join '; ') -RecommendedCommand 'git status --short'
    } else {
        Add-CheckResult -Name 'Git working tree' -Status 'PASS' -Issue 'Working tree is clean.' -ExpectedSolution 'None.' -Evidence 'git status --short returned no changes'
    }

    $branchLine = (git status -sb | Select-Object -First 1)
    if ($branchLine -match '\[ahead ([0-9]+), behind ([0-9]+)\]') {
        Add-CheckResult -Name 'Git branch sync' -Status 'WARN' -Issue 'Local branch has diverged from origin.' -ExpectedSolution 'Review local commits and upstream commits before the next push or pull. Rebase or merge intentionally.' -Evidence $branchLine -RecommendedCommand 'git log --oneline --decorate --left-right HEAD...origin/main'
    } elseif ($branchLine -match '\[ahead ([0-9]+)\]') {
        Add-CheckResult -Name 'Git branch sync' -Status 'WARN' -Issue 'Local branch is ahead of origin.' -ExpectedSolution 'This is expected after a checkpoint or merge commit. Push a temp branch if you need to share it; do not push main directly.' -Evidence $branchLine -RecommendedCommand '.\git-menu.ps1'
    } elseif ($branchLine -match '\[behind ([0-9]+)\]') {
        Add-CheckResult -Name 'Git branch sync' -Status 'WARN' -Issue 'Local branch is behind origin.' -ExpectedSolution 'Run the safe sync helper before smoke testing code that depends on latest upstream changes.' -Evidence $branchLine -RecommendedCommand '.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint'
    } else {
        Add-CheckResult -Name 'Git branch sync' -Status 'PASS' -Issue 'Local branch matches origin state closely enough for local verification.' -ExpectedSolution 'None.' -Evidence $branchLine
    }
}

$dockerReady = $true
docker version *> $null
if ($LASTEXITCODE -ne 0) {
    $dockerReady = $false
    Add-CheckResult -Name 'Docker availability' -Status 'FAIL' -Issue 'Docker CLI or Docker Desktop is not ready.' -ExpectedSolution 'Start Docker Desktop, wait for it to finish initializing, then rerun this script or start_platform.ps1.' -Evidence 'docker version failed' -RecommendedCommand (Get-StartCommand)
} else {
    Add-CheckResult -Name 'Docker availability' -Status 'PASS' -Issue 'Docker is reachable.' -ExpectedSolution 'None.' -Evidence 'docker version succeeded'
}

if ($dockerReady) {
    $composePs = @(docker compose ps --format json 2>$null)
    if ($LASTEXITCODE -eq 0 -and $composePs.Count -gt 0) {
        $services = @()
        foreach ($line in $composePs) {
            try {
                $entry = $line | ConvertFrom-Json
                $services += "$($entry.Service): $($entry.State)"
            } catch {
            }
        }

        if ($services.Count -gt 0) {
            $nonRunning = @($services | Where-Object { $_ -notmatch ': running$' })
            if ($nonRunning.Count -gt 0) {
                Add-CheckResult -Name 'Docker compose services' -Status 'WARN' -Issue 'One or more compose services are not running.' -ExpectedSolution 'Use status_platform.ps1 or docker compose ps to inspect the stopped service, then start the platform or review container logs.' -Evidence ($services -join '; ') -RecommendedCommand '.\scripts\status_platform.ps1'
            } else {
                Add-CheckResult -Name 'Docker compose services' -Status 'PASS' -Issue 'Compose services are running.' -ExpectedSolution 'None.' -Evidence ($services -join '; ')
            }
        } else {
            Add-CheckResult -Name 'Docker compose services' -Status 'WARN' -Issue 'Compose returned no service records.' -ExpectedSolution 'If the platform should be running, start it with start_platform.ps1 and then rerun this script.' -Evidence 'docker compose ps returned no parsable services' -RecommendedCommand (Get-StartCommand)
        }
    } else {
        Add-CheckResult -Name 'Docker compose services' -Status 'WARN' -Issue 'Could not read docker compose service state.' -ExpectedSolution 'Run docker compose ps manually and verify the compose project is available from this repo root.' -Evidence 'docker compose ps failed or returned no JSON output' -RecommendedCommand 'docker compose ps'
    }
}

$health = Test-HttpJson -Url "http://127.0.0.1:$ApiPort/health"
if ($health.Ok) {
    Add-CheckResult -Name 'API health' -Status 'PASS' -Issue 'Primary API health endpoint responded.' -ExpectedSolution 'None.' -Evidence (ConvertTo-Json $health.Body -Compress -Depth 4)
} else {
    $apiOwner = Get-ListeningProcessName -Port $ApiPort
    if ($apiOwner) {
        Add-CheckResult -Name 'API health' -Status 'FAIL' -Issue 'Something is listening on the API port, but the SOC API health endpoint did not respond successfully.' -ExpectedSolution 'Stop the conflicting process or start the platform on a different -ApiPort, then rerun the health check.' -Evidence "Port $ApiPort owned by $apiOwner; $($health.Error)" -RecommendedCommand (Get-TroubleshootCommand)
    } else {
        Add-CheckResult -Name 'API health' -Status 'FAIL' -Issue 'SOC API is not healthy or not running.' -ExpectedSolution 'Start the platform with start_platform.ps1, then rerun this script. If startup fails, inspect docker compose logs for api-service.' -Evidence $health.Error -RecommendedCommand (Get-StartCommand)
    }
}

$compatHealth = Test-HttpJson -Url "http://127.0.0.1:$ApiPort/api/health"
if ($compatHealth.Ok) {
    Add-CheckResult -Name 'API compatibility health' -Status 'PASS' -Issue 'Compatibility health endpoint responded.' -ExpectedSolution 'None.' -Evidence (ConvertTo-Json $compatHealth.Body -Compress -Depth 4)
} else {
    Add-CheckResult -Name 'API compatibility health' -Status 'WARN' -Issue 'Compatibility health endpoint did not respond.' -ExpectedSolution 'If /health passes, this may be a route regression. Compare API routing changes before running frontend smoke tests.' -Evidence $compatHealth.Error -RecommendedCommand (Get-TroubleshootCommand)
}

$postgresListener = Get-ListeningProcessName -Port $PostgresHostPort
if ($postgresListener) {
    Add-CheckResult -Name 'PostgreSQL port' -Status 'PASS' -Issue 'A process is listening on the configured PostgreSQL host port.' -ExpectedSolution 'None.' -Evidence "Port $PostgresHostPort owned by $postgresListener"
} else {
    Add-CheckResult -Name 'PostgreSQL port' -Status 'FAIL' -Issue 'No process is listening on the configured PostgreSQL host port.' -ExpectedSolution 'Start the platform or check whether a different -PostgresHostPort was used.' -Evidence "Nothing listening on port $PostgresHostPort" -RecommendedCommand (Get-StartCommand)
}

$ollama = Test-HttpJson -Url 'http://127.0.0.1:11434/api/tags'
if ($ollama.Ok) {
    $models = @($ollama.Body.models)
    if ($models.Count -gt 0) {
        Add-CheckResult -Name 'Ollama service' -Status 'PASS' -Issue 'Ollama is reachable and has at least one model installed.' -ExpectedSolution 'None.' -Evidence ("Models: " + (($models | ForEach-Object { $_.name }) -join ', '))
    } else {
        Add-CheckResult -Name 'Ollama service' -Status 'WARN' -Issue 'Ollama is reachable, but no models are installed.' -ExpectedSolution 'AI analysis will fail until a model is installed. Keep using the platform for non-AI features or install a local model using the documented fallback path.' -Evidence 'Ollama responded with zero models' -RecommendedCommand '.\docs\RESTART.md'
    }
} else {
    Add-CheckResult -Name 'Ollama service' -Status 'WARN' -Issue 'Ollama is not reachable.' -ExpectedSolution 'Start Ollama only if AI-analysis features are needed. Non-AI smoke tests can still proceed.' -Evidence $ollama.Error -RecommendedCommand '.\scripts\start_platform.ps1 -EnsureOllama'
}

$lockFilePath = Join-Path $SharedDumpDir 'db_in_use.lock.json'
if (Test-Path $lockFilePath) {
    try {
        $lockData = Get-Content $lockFilePath -Raw | ConvertFrom-Json
        Add-CheckResult -Name 'Shared handoff lock' -Status 'WARN' -Issue 'A shared DB lock file is present.' -ExpectedSolution 'If another user or machine owns the lock, avoid restore/export operations until coordinated. If this lock is stale, clear it intentionally.' -Evidence (ConvertTo-Json $lockData -Compress -Depth 4) -RecommendedCommand '.\scripts\status_platform.ps1'
    } catch {
        Add-CheckResult -Name 'Shared handoff lock' -Status 'WARN' -Issue 'A shared DB lock file exists but could not be parsed.' -ExpectedSolution 'Inspect the lock file manually before restore/export operations; remove it only if you confirm it is stale.' -Evidence $lockFilePath -RecommendedCommand '.\scripts\status_platform.ps1'
    }
} else {
    Add-CheckResult -Name 'Shared handoff lock' -Status 'PASS' -Issue 'No shared DB lock file is present.' -ExpectedSolution 'None.' -Evidence $lockFilePath
}

$dumpMetadataPath = Join-Path $SharedDumpDir 'current_soc_platform_dump.metadata.json'
if (Test-Path $dumpMetadataPath) {
    try {
        $dumpMetadata = Get-Content $dumpMetadataPath -Raw | ConvertFrom-Json
        Add-CheckResult -Name 'Shared dump metadata' -Status 'PASS' -Issue 'Shared dump metadata is present.' -ExpectedSolution 'None.' -Evidence (ConvertTo-Json $dumpMetadata -Compress -Depth 4)
    } catch {
        Add-CheckResult -Name 'Shared dump metadata' -Status 'WARN' -Issue 'Shared dump metadata exists but could not be parsed.' -ExpectedSolution 'Inspect the metadata file manually before relying on the shared handoff state.' -Evidence $dumpMetadataPath -RecommendedCommand '.\scripts\status_platform.ps1'
    }
} else {
    Add-CheckResult -Name 'Shared dump metadata' -Status 'WARN' -Issue 'Shared dump metadata file is missing.' -ExpectedSolution 'If you expect shared handoff state, run stop_platform.ps1 from the machine currently holding the latest runtime state to export a fresh dump.' -Evidence $dumpMetadataPath -RecommendedCommand '.\scripts\stop_platform.ps1'
}

if ($RunSmokeTest) {
    Invoke-SmokeTests -BaseUrl "http://127.0.0.1:$ApiPort"
}

$sortedResults = $results | Sort-Object @{ Expression = {
    switch ($_.Status) {
        'FAIL' { 0 }
        'WARN' { 1 }
        default { 2 }
    }
} }, Check

if ($AsJson) {
    $output = [pscustomobject]@{
        repoRoot = $RepoRoot
        apiPort = $ApiPort
        postgresHostPort = $PostgresHostPort
        generatedAt = (Get-Date).ToString('o')
        results = $sortedResults
    }
    $output | ConvertTo-Json -Depth 6
} else {
    Write-Host ''
    Write-Host 'Troubleshooting Summary:'
    if ($ShowEvidence) {
        $sortedResults | Format-Table Check, Status, Issue, ExpectedSolution, RecommendedCommand, Evidence -Wrap -AutoSize
    } else {
        $sortedResults | Format-Table Check, Status, Issue, ExpectedSolution, RecommendedCommand -Wrap -AutoSize
    }
}

$failCount = @($results | Where-Object Status -eq 'FAIL').Count
$warnCount = @($results | Where-Object Status -eq 'WARN').Count
$passCount = @($results | Where-Object Status -eq 'PASS').Count

if (-not $AsJson) {
    Write-Host ''
    Write-Host "PASS: $passCount  WARN: $warnCount  FAIL: $failCount"
    Write-NextActions -CheckResults $sortedResults
}

if ($failCount -gt 0) {
    exit 1
}

if ($warnCount -gt 0) {
    exit 2
}

exit 0