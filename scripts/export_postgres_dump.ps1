param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$OutputPath = "postgres_dump_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql"
)

if (-not $DatabaseUrl) {
    Write-Error "DATABASE_URL is not set. Provide -DatabaseUrl or set the environment variable."
    exit 1
}

$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) {
    Write-Error "pg_dump was not found in PATH. Install PostgreSQL client tools or add them to PATH."
    exit 1
}

Write-Host "[*] Exporting PostgreSQL dump to $OutputPath"
& $pgDump.Source --no-owner --no-privileges --file $OutputPath $DatabaseUrl

if ($LASTEXITCODE -ne 0) {
    Write-Error "pg_dump failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[+] Dump complete: $OutputPath"