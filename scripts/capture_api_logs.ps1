param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".." )).Path,
    [string]$ServiceName = "api-service",
    [string]$Since = "1h"  # e.g. 10m, 1h, 24h
)

Set-Location $RepoRoot

$logsDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logsDir "${ServiceName}_$timestamp.log"

Write-Host "[*] Capturing Docker logs for '$ServiceName' since $Since ..."

try {
    docker logs --since $Since $ServiceName *> $logFile
    Write-Host "[+] Logs written to $logFile"
} catch {
    Write-Error "Failed to capture logs: $($_.Exception.Message)"
}
