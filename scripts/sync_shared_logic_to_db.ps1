param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DatabaseUrl = $env:DATABASE_URL
)

Set-Location $RepoRoot

if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
}

$env:DATABASE_URL = $DatabaseUrl

Write-Host "[*] Syncing shared logic from repo files into database..."
Write-Host "[*] DATABASE_URL=$DatabaseUrl"

$sampleRules = Join-Path $RepoRoot "sample_rules.json"
$supportiveRules = Join-Path $RepoRoot "supportive_rules.json"
$exportedSupportiveRules = Join-Path $RepoRoot "local-backups\shared-logic-export\supportive_rules.exported.json"

if (Test-Path $sampleRules) {
    python .\Tools\es_rules_importer\es_rules_importer.py --import $sampleRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $supportiveRules) {
    python .\Tools\es_rules_importer\es_rules_importer.py --import $supportiveRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $exportedSupportiveRules) {
    Write-Host "[*] Importing exported supportive queries snapshot from $exportedSupportiveRules"
    python .\Tools\es_rules_importer\es_rules_importer.py --import $exportedSupportiveRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[+] Shared logic sync complete"