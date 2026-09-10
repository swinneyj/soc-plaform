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
$exportedSupportiveRules = Join-Path $RepoRoot (Join-Path "local-backups" (Join-Path "shared-logic-export" "supportive_rules.exported.json"))
$aliasFile = Join-Path $RepoRoot "placeholder_aliases.json"
$exportedAliasFile = Join-Path $RepoRoot (Join-Path "local-backups" (Join-Path "shared-logic-export" "placeholder_aliases.exported.json"))

$importer = Join-Path $RepoRoot (Join-Path "Tools" (Join-Path "es_rules_importer" "es_rules_importer.py"))

if (Test-Path $sampleRules) {
    python $importer --import $sampleRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $supportiveRules) {
    python $importer --import $supportiveRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $exportedSupportiveRules) {
    Write-Host "[*] Importing exported supportive queries snapshot from $exportedSupportiveRules"
    python $importer --import $exportedSupportiveRules
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $aliasFile) {
    Write-Host "[*] Importing placeholder aliases from $aliasFile"
    python .\scripts\import_placeholder_aliases.py --input $aliasFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if (Test-Path $exportedAliasFile) {
    Write-Host "[*] Importing exported placeholder aliases snapshot from $exportedAliasFile"
    python .\scripts\import_placeholder_aliases.py --input $exportedAliasFile
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Write-Host "[+] Shared logic sync complete"
