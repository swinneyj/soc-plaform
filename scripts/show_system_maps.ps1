param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$dashboardPath = Join-Path $RepoRoot 'docs\system_maps_dashboard.html'

if (-not (Test-Path $dashboardPath)) {
    Write-Error "System maps dashboard not found: $dashboardPath"
    exit 1
}

Write-Host "Opening SOC System Maps dashboard..."
Start-Process $dashboardPath