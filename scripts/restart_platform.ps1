param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path,
    [int]$PostgresHostPort = 5433,
    [int]$ApiPort = 8000,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [switch]$OpenBrowser,
    [switch]$StartCommander,
    [switch]$EnsureOllama,
    [switch]$SkipSharedDumpRestore,
    [switch]$SkipSharedDumpExport,
    [switch]$IgnoreExistingLock
)

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Restarting SOC Platform"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"

# First stop the current stack (containers + optional Ollama)
Write-Host "[*] Stopping existing SOC Platform containers..."
& (Join-Path $PSScriptRoot "stop_platform.ps1") -RepoRoot $RepoRoot -SharedDumpDir $SharedDumpDir -SkipSharedDumpExport:$SkipSharedDumpExport -IgnoreExistingLock:$IgnoreExistingLock
$stopExitCode = $LASTEXITCODE
if ($stopExitCode -ne 0) {
    Write-Error "stop_platform.ps1 failed with exit code $stopExitCode. Aborting restart."
    exit $stopExitCode
}

Write-Host "[*] Starting SOC Platform containers..."
& (Join-Path $PSScriptRoot "start_platform.ps1") -RepoRoot $RepoRoot -PostgresHostPort $PostgresHostPort -ApiPort $ApiPort -SharedDumpDir $SharedDumpDir -OpenBrowser:$OpenBrowser -StartCommander:$StartCommander -EnsureOllama:$EnsureOllama -SkipSharedDumpRestore:$SkipSharedDumpRestore -IgnoreExistingLock:$IgnoreExistingLock
$startExitCode = $LASTEXITCODE
if ($startExitCode -ne 0) {
    Write-Error "start_platform.ps1 failed with exit code $startExitCode. Restart incomplete."
    exit $startExitCode
}

Write-Host "[+] SOC Platform restart complete"