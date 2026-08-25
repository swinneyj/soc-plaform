param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$ContainerName = "soc-postgres",
    [switch]$SkipResetSchema
)

if (-not (Test-Path $InputPath)) {
    Write-Error "Input dump file not found: $InputPath"
    exit 1
}

if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
}

$psql = Get-Command psql -ErrorAction SilentlyContinue

Write-Host "[*] Restoring PostgreSQL dump from $InputPath"

if (-not $SkipResetSchema) {
    Write-Host "[*] Resetting public schema before restore..."
    if ($psql) {
        & $psql.Source $DatabaseUrl -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to reset PostgreSQL schema before restore."
            exit $LASTEXITCODE
        }
    } else {
        $docker = Get-Command docker -ErrorAction SilentlyContinue
        if (-not $docker) {
            Write-Error "Neither psql nor docker is available. Install PostgreSQL client tools or Docker."
            exit 1
        }
        docker exec $ContainerName psql -U soc_platform -d soc_platform -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to reset PostgreSQL schema before restore."
            exit $LASTEXITCODE
        }
    }
}

if ($psql) {
    & $psql.Source $DatabaseUrl -f $InputPath
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Error "Neither psql nor docker is available. Install PostgreSQL client tools or Docker."
        exit 1
    }
    Get-Content $InputPath | docker exec -i $ContainerName psql -U soc_platform -d soc_platform
}

if ($LASTEXITCODE -ne 0) {
    Write-Error "psql restore failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "[+] Restore complete"