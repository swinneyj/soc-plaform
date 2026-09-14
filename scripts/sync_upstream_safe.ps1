param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$UpstreamBranch = "main",
    [int]$ApiPort = 8000,
    [switch]$CheckOnly,
    [switch]$AutoCheckpoint,
    [string]$CheckpointMessage = "Pre-sync checkpoint",
    [switch]$SyncSharedLogic
)

function Invoke-Git {
    param([string[]]$GitArgs)
    & git $GitArgs | Out-Host
    return [int]$LASTEXITCODE
}

function Test-HttpJson {
    param([string]$Url)
    try {
        return @{ Ok = $true; Body = (Invoke-RestMethod -Uri $Url -TimeoutSec 5) }
    } catch {
        return @{ Ok = $false; Body = $_.Exception.Message }
    }
}

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Sync Upstream Into Working Copy"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"
Write-Host "[*] Target branch: origin/$UpstreamBranch"

$gitExitCode = Invoke-Git -GitArgs @('fetch', 'origin')
if ($gitExitCode -ne 0) {
    Write-Error "git fetch origin failed."
    exit $gitExitCode
}

$behindCommits = git log --oneline HEAD..origin/$UpstreamBranch
$statusOutput = git status --short --branch

Write-Host ""
Write-Host "[*] Working tree status:"
Write-Host $statusOutput

if ($behindCommits) {
    Write-Host ""
    Write-Host "[*] Upstream commits waiting to be pulled:"
    Write-Host $behindCommits
} else {
    Write-Host ""
    Write-Host "[+] Working copy is already up to date with origin/$UpstreamBranch"
}

if ($CheckOnly) {
    Write-Host ""
    Write-Host "[+] Check-only mode complete. No local changes were made."
    exit 0
}

if (-not $behindCommits) {
    exit 0
}

$dirtyLines = git status --porcelain
if ($dirtyLines) {
    if (-not $AutoCheckpoint) {
        Write-Warning "Local changes are present. Re-run with -AutoCheckpoint or commit manually before pulling."
        exit 2
    }

    Write-Host ""
    Write-Host "[*] Creating local checkpoint commit before pull..."
    $gitExitCode = Invoke-Git -GitArgs @('add', '-A')
    if ($gitExitCode -ne 0) {
        Write-Error "git add -A failed."
        exit $gitExitCode
    }

    $gitExitCode = Invoke-Git -GitArgs @('commit', '-m', $CheckpointMessage)
    if ($gitExitCode -ne 0) {
        Write-Error "git commit failed. Resolve the working tree and try again."
        exit $gitExitCode
    }
}

Write-Host ""
Write-Host "[*] Pulling origin/$UpstreamBranch into the working copy..."
$gitExitCode = Invoke-Git -GitArgs @('pull', 'origin', $UpstreamBranch)
if ($gitExitCode -ne 0) {
    Write-Warning "git pull stopped. Resolve conflicts, then commit the merge."
    exit $gitExitCode
}

if ($SyncSharedLogic) {
    Write-Host ""
    Write-Host "[*] Syncing repo-managed logic into the database..."
    & (Join-Path $PSScriptRoot 'sync_shared_logic_to_db.ps1') -RepoRoot $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Shared logic sync failed after pull."
        exit $LASTEXITCODE
    }
}

Write-Host ""
Write-Host "[*] Running post-sync health checks..."
$health = Test-HttpJson -Url "http://127.0.0.1:$ApiPort/health"
$dbStats = Test-HttpJson -Url "http://127.0.0.1:$ApiPort/api/db/stats"

Write-Host "API /health: $($health.Ok)"
if ($health.Ok) {
    Write-Host ($health.Body | ConvertTo-Json -Depth 4)
} else {
    Write-Warning $health.Body
}

Write-Host "API /api/db/stats: $($dbStats.Ok)"
if ($dbStats.Ok) {
    Write-Host ($dbStats.Body | ConvertTo-Json -Depth 6)
} else {
    Write-Warning $dbStats.Body
}

Write-Host ""
Write-Host "[+] Upstream sync complete."