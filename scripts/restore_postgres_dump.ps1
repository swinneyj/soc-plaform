param(
    [Parameter(Mandatory = $true)]
    [string]$InputPath,
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$ContainerName = "soc-postgres",
    [switch]$SkipResetSchema,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

Set-Location $RepoRoot

if (-not (Test-Path -LiteralPath $InputPath)) {
    Write-Error "Input dump file not found: $InputPath"
    exit 1
}

$inputFile = Get-Item -LiteralPath $InputPath
$resolvedInputPath = $inputFile.FullName
if ($inputFile.Length -le 0) {
    Write-Error "Input dump file is empty and restore was aborted: $InputPath"
    exit 1
}

if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://soc_platform@localhost:5433/soc_platform"
}

# Windows PowerShell commonly creates UTF-16 dumps when output is redirected.
# PostgreSQL expects UTF-8 SQL, so normalize BOM-marked input before invoking
# either a host psql client or the containerized client. The source dump is
# never modified; the temporary normalized copy is removed after the restore.
$restorePath = $resolvedInputPath
$temporaryRestorePath = $null
$bytes = [System.IO.File]::ReadAllBytes($resolvedInputPath)
if (($bytes.Length -ge 2) -and (($bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) -or ($bytes[0] -eq 0xFE -and $bytes[1] -eq 0xFF))) {
    $encoding = if ($bytes[0] -eq 0xFF) { [System.Text.Encoding]::Unicode } else { [System.Text.Encoding]::BigEndianUnicode }
    $temporaryRestorePath = Join-Path ([System.IO.Path]::GetTempPath()) ("soc_platform_restore_{0}.sql" -f ([guid]::NewGuid().ToString("N")))
    $utf8 = New-Object -TypeName System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($temporaryRestorePath, $encoding.GetString($bytes, 2, $bytes.Length - 2), $utf8)
    $restorePath = $temporaryRestorePath
    Write-Host "[*] Converted UTF-16 dump to a temporary UTF-8 copy for PostgreSQL."
}

$psql = Get-Command psql -ErrorAction SilentlyContinue

Write-Host "[*] Restoring PostgreSQL dump from $resolvedInputPath"

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
        docker compose exec -T postgres psql -U soc_platform -d soc_platform -c "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Failed to reset PostgreSQL schema before restore."
            exit $LASTEXITCODE
        }
    }
}

if ($psql) {
    & $psql.Source $DatabaseUrl -v ON_ERROR_STOP=1 -f $restorePath
} else {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Write-Error "Neither psql nor docker is available. Install PostgreSQL client tools or Docker."
        exit 1
    }
    Get-Content -LiteralPath $restorePath -Raw | docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U soc_platform -d soc_platform
}

if ($LASTEXITCODE -ne 0) {
    if ($temporaryRestorePath -and (Test-Path -LiteralPath $temporaryRestorePath)) { Remove-Item -LiteralPath $temporaryRestorePath -Force }
    Write-Error "psql restore failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

if ($temporaryRestorePath -and (Test-Path -LiteralPath $temporaryRestorePath)) { Remove-Item -LiteralPath $temporaryRestorePath -Force }

Write-Host "[+] Restore complete"
