param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$PostgresHostPort = 5433,
    [int]$ApiPort = 8000,
    [string]$SharedDumpDir = "Z:\PAX DNA SOC\01 Tools\11 SOC Automation Handoff",
    [switch]$OpenBrowser,
    [switch]$StartCommander,
    [switch]$EnsureOllama,
    [switch]$SkipSharedDumpRestore,
    [switch]$IgnoreExistingLock
)

$script:LockFilePath = Join-Path $SharedDumpDir "db_in_use.lock.json"

function Test-HttpReady {
    param([string]$Url)
    try {
        $null = Invoke-RestMethod -Uri $Url -TimeoutSec 3
        return $true
    } catch {
        return $false
    }
}

function Wait-ForHttp {
    param(
        [string]$Url,
        [int]$MaxAttempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        if (Test-HttpReady -Url $Url) {
            return $true
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

function Wait-ForPostgres {
    param(
        [int]$MaxAttempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        docker compose exec -T postgres pg_isready -U soc_platform -d soc_platform *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds $DelaySeconds
    }
    return $false
}

function Ensure-DockerRunning {
    docker version *> $null
    if ($LASTEXITCODE -eq 0) {
        return $true
    }

    Write-Host "[*] Docker is not ready. Attempting to start Docker Desktop..."
    $dockerDesktopPaths = @(
        "C:\Program Files\Docker\Docker\Docker Desktop.exe",
        "C:\Program Files (x86)\Docker\Docker\Docker Desktop.exe"
    )

    $dockerDesktop = $dockerDesktopPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $dockerDesktop) {
        Write-Error "Docker Desktop was not found. Start Docker manually and try again."
        return $false
    }

    Start-Process -FilePath $dockerDesktop

    for ($attempt = 1; $attempt -le 60; $attempt++) {
        docker version *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        Start-Sleep -Seconds 2
    }

    Write-Error "Docker Desktop did not become ready in time."
    return $false
}

function Ensure-OllamaRunning {
    if (Test-HttpReady -Url "http://127.0.0.1:11434/api/tags") {
        Write-Host "[+] Ollama is already running"
        return $true
    }

    $ollama = Get-Command ollama -ErrorAction SilentlyContinue
    if (-not $ollama) {
        Write-Warning "Ollama CLI not found. Start Ollama manually if AI analysis is needed."
        return $false
    }

    Write-Host "[*] Starting Ollama service..."
    Start-Process -FilePath $ollama.Source -ArgumentList "serve" -WindowStyle Hidden

    if (Wait-ForHttp -Url "http://127.0.0.1:11434/api/tags" -MaxAttempts 30 -DelaySeconds 2) {
        Write-Host "[+] Ollama is ready"
        return $true
    }

    Write-Warning "Ollama did not become ready in time. The platform can still run, but AI analysis may fail until Ollama is started."
    return $false
}

function Get-LockPayload {
    return @{
        username = $env:USERNAME
        computer = $env:COMPUTERNAME
        repo_root = $RepoRoot
        timestamp = (Get-Date).ToString("o")
        postgres_host_port = $PostgresHostPort
        api_port = $ApiPort
    }
}

function Read-LockFile {
    if (-not (Test-Path $script:LockFilePath)) {
        return $null
    }
    try {
        return Get-Content $script:LockFilePath -Raw | ConvertFrom-Json
    } catch {
        Write-Warning "Existing lock file could not be parsed: $script:LockFilePath"
        return $null
    }
}

function Acquire-ShareLock {
    if (-not (Test-Path $SharedDumpDir)) {
        New-Item -ItemType Directory -Path $SharedDumpDir -Force | Out-Null
    }

    $existingLock = Read-LockFile
    if ($existingLock) {
        $sameOwner = ($existingLock.username -eq $env:USERNAME) -and ($existingLock.computer -eq $env:COMPUTERNAME)
        if (-not $sameOwner -and -not $IgnoreExistingLock) {
            Write-Error "A shared DB lock already exists for user '$($existingLock.username)' on '$($existingLock.computer)' since $($existingLock.timestamp). Use -IgnoreExistingLock only if you intentionally want to take over."
            return $false
        }
        if ($sameOwner) {
            Write-Host "[*] Reusing existing lock owned by this user/machine"
        } elseif ($IgnoreExistingLock) {
            Write-Warning "Overriding existing lock from another user/machine"
        }
    }

    $payload = Get-LockPayload | ConvertTo-Json
    Set-Content -Path $script:LockFilePath -Value $payload -Encoding UTF8
    Write-Host "[+] Share lock acquired: $script:LockFilePath"
    return $true
}

function Release-ShareLock {
    if (-not (Test-Path $script:LockFilePath)) {
        return
    }
    $existingLock = Read-LockFile
    if ($existingLock -and $existingLock.username -eq $env:USERNAME -and $existingLock.computer -eq $env:COMPUTERNAME) {
        Remove-Item $script:LockFilePath -Force
        Write-Host "[+] Share lock released"
    }
}

Set-Location $RepoRoot

Write-Host "============================================"
Write-Host "  Starting SOC Platform"
Write-Host "============================================"
Write-Host "[*] Repo root: $RepoRoot"

if (-not (Ensure-DockerRunning)) {
    exit 1
}

if ($EnsureOllama) {
    Ensure-OllamaRunning | Out-Null
}

if (-not (Acquire-ShareLock)) {
    exit 1
}

$env:POSTGRES_HOST_PORT = "$PostgresHostPort"
$env:API_HOST_PORT = "$ApiPort"
$env:COMPOSE_DATABASE_URL = "postgresql+psycopg://soc_platform@postgres:5432/soc_platform"
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:$PostgresHostPort/soc_platform"

Write-Host "[*] Starting postgres and redis via docker compose..."
docker compose up -d postgres redis
if ($LASTEXITCODE -ne 0) {
    Release-ShareLock
    Write-Error "docker compose up failed."
    exit $LASTEXITCODE
}

Write-Host "[*] Waiting for PostgreSQL to become ready..."
if (-not (Wait-ForPostgres -MaxAttempts 60 -DelaySeconds 2)) {
    Release-ShareLock
    Write-Error "PostgreSQL did not become ready in time."
    exit 1
}

if (-not $SkipSharedDumpRestore) {
    $sharedDumpPath = Join-Path $SharedDumpDir "current_soc_platform_dump.sql"
    if (Test-Path $sharedDumpPath) {
        Write-Host "[*] Restoring shared dump from $sharedDumpPath"
        & (Join-Path $PSScriptRoot "restore_db_dump_from_share.ps1") -ShareDir $SharedDumpDir -DatabaseUrl $env:DATABASE_URL
        if ($LASTEXITCODE -ne 0) {
            Release-ShareLock
            exit $LASTEXITCODE
        }
    } else {
        Write-Host "[*] No shared dump found. Skipping restore."
    }
}

Write-Host "[*] Syncing shared rules and supportive queries from repo..."
& (Join-Path $PSScriptRoot "sync_shared_logic_to_db.ps1") -RepoRoot $RepoRoot -DatabaseUrl $env:DATABASE_URL
if ($LASTEXITCODE -ne 0) {
    Release-ShareLock
    exit $LASTEXITCODE
}

Write-Host "[*] Starting api-service via docker compose..."
docker compose up -d api-service
if ($LASTEXITCODE -ne 0) {
    Release-ShareLock
    Write-Error "docker compose up for api-service failed."
    exit $LASTEXITCODE
}

$healthUrl = "http://127.0.0.1:$ApiPort/health"
Write-Host "[*] Waiting for API health at $healthUrl"
if (-not (Wait-ForHttp -Url $healthUrl -MaxAttempts 60 -DelaySeconds 2)) {
    Release-ShareLock
    Write-Error "API did not become healthy in time."
    exit 1
}

Write-Host "[+] API is healthy"
Write-Host "[+] DATABASE_URL=$($env:DATABASE_URL)"

if ($OpenBrowser) {
    Start-Process "http://127.0.0.1:$ApiPort"
}

if ($StartCommander) {
    Write-Host "[*] Starting SOC Commander..."
    python .\commander.py
}