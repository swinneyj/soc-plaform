param(
    [string]$ShareDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [string]$FileName = "current_soc_platform_dump.sql",
    [string]$DatabaseUrl = $env:DATABASE_URL
)

$inputPath = Join-Path $ShareDir $FileName

if (-not (Test-Path $inputPath)) {
    Write-Error "Shared dump file not found: $inputPath"
    exit 1
}

& (Join-Path $PSScriptRoot "restore_postgres_dump.ps1") -InputPath $inputPath -DatabaseUrl $DatabaseUrl

if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[+] Shared dump restored from: $inputPath"