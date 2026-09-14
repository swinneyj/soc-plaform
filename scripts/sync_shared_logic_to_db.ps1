param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DatabaseUrl = $env:DATABASE_URL
)

Set-Location $RepoRoot

if (-not $DatabaseUrl) {
    if (-not $env:POSTGRES_PASSWORD) {
        throw "Set DATABASE_URL or POSTGRES_PASSWORD before syncing shared logic."
    }
    $encodedPassword = [System.Uri]::EscapeDataString($env:POSTGRES_PASSWORD)
    $databaseUser = if ($env:POSTGRES_USER) { $env:POSTGRES_USER } else { "soc_platform" }
    $databaseName = if ($env:POSTGRES_DB) { $env:POSTGRES_DB } else { "soc_platform" }
    $DatabaseUrl = "postgresql+psycopg://$databaseUser`:$encodedPassword@localhost`:5433/$databaseName"
}

$env:DATABASE_URL = $DatabaseUrl

Write-Host "[*] Syncing shared logic from repo files into database..."
Write-Host "[*] DATABASE_URL configured (password hidden)"

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
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Placeholder alias import from $aliasFile failed (exit code $LASTEXITCODE). Continuing without aliases."
    }
}

if (Test-Path $exportedAliasFile) {
    Write-Host "[*] Importing exported placeholder aliases snapshot from $exportedAliasFile"
    python .\scripts\import_placeholder_aliases.py --input $exportedAliasFile
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Placeholder alias import from $exportedAliasFile failed (exit code $LASTEXITCODE). Continuing without aliases."
    }
}

Write-Host "[+] Shared logic sync complete"
