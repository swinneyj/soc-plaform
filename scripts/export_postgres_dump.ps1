param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$OutputPath = "postgres_dump_$(Get-Date -Format 'yyyyMMdd_HHmmss').sql",
    [string]$ContainerName = "soc-postgres",
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

Set-Location $RepoRoot

if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
}

$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue

Write-Host "[*] Exporting PostgreSQL dump to $OutputPath"

if ($pgDump) {
    & $pgDump.Source --no-owner --no-privileges --file $OutputPath $DatabaseUrl
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Error "Neither pg_dump nor docker is available. Install PostgreSQL client tools or Docker."
        exit 1
    }
    docker compose exec -T postgres pg_dump -U soc_platform -d soc_platform --no-owner --no-privileges > $OutputPath
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "PostgreSQL export failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[+] Dump complete: $OutputPath"