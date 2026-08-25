param(
    [string]$ShareDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [string]$FileName = "current_soc_platform_dump.sql",
    [string]$DatabaseUrl = $env:DATABASE_URL
)

$outputPath = Join-Path $ShareDir $FileName

if (-not (Test-Path $ShareDir)) {
    New-Item -ItemType Directory -Path $ShareDir -Force | Out-Null
}

& (Join-Path $PSScriptRoot "export_postgres_dump.ps1") -DatabaseUrl $DatabaseUrl -OutputPath $outputPath

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[+] Shared dump updated: $outputPath"