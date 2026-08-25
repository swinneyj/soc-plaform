param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DatabaseUrl = $env:DATABASE_URL
)

if (-not (Test-Path $InputPath)) {
    Write-Error "Input dump file not found: $InputPath"
    exit 1
}

if (-not $DatabaseUrl) {
    Write-Error "DATABASE_URL is not set. Provide -DatabaseUrl or set the environment variable."
    exit 1
}

$psql = Get-Command psql -ErrorAction SilentlyContinue
if (-not $psql) {
    Write-Error "psql was not found in PATH. Install PostgreSQL client tools or add them to PATH."
    exit 1
}

Write-Host "[*] Restoring PostgreSQL dump from $InputPath"
& $psql.Source $DatabaseUrl -f $InputPath

if ($LASTEXITCODE -ne 0) {
    Write-Error "psql restore failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[+] Restore complete"