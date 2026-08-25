param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [int]$PostgresHostPort = 5433,
    [int]$ApiPort = 8000,
    [switch]$OpenBrowser,
    [switch]$StartCommander,
    [switch]$EnsureOllama
)

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

$env:POSTGRES_HOST_PORT = "$PostgresHostPort"
$env:API_HOST_PORT = "$ApiPort"
$env:COMPOSE_DATABASE_URL = "postgresql+psycopg://soc_platform@postgres:5432/soc_platform"
$env:DATABASE_URL = "postgresql+psycopg://soc_platform@localhost:$PostgresHostPort/soc_platform"

Write-Host "[*] Starting postgres, redis, and api-service via docker compose..."
docker compose up -d postgres redis api-service
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose up failed."
    exit $LASTEXITCODE
}

$healthUrl = "http://127.0.0.1:$ApiPort/health"
Write-Host "[*] Waiting for API health at $healthUrl"
if (-not (Wait-ForHttp -Url $healthUrl -MaxAttempts 60 -DelaySeconds 2)) {
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