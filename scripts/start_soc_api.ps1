#!/usr/bin/env pwsh
# SOC Platform API self-start script (cross-platform via PowerShell Core).
# Order: wait for Postgres -> start Ollama if absent -> start API (uvicorn).
# Safe to run repeatedly: exits quietly if the API port is already served.
#
# Usage:  pwsh -File scripts/start_soc_api.ps1
#         .\scripts\start_soc_api.ps1
#
# Env vars (all optional):
#   SOC_API_PORT     - API listen port (default 8000)
#   SOC_API_HOST     - API bind host (default 127.0.0.1)
#   OLLAMA_PORT      - Ollama port (default 11434)
#   SOC_ENV_FILE     - .env path (default <repo>/.env)
#   SOC_VENV_PY      - Python to run uvicorn (default <repo>/.venv/bin/python / .venv\Scripts\python.exe)
#   SOC_LOG_DIR      - Log directory (default <repo>/logs)
#
# Differs from start_soc_api.sh:
#   - Uses .NET TcpClient instead of nc -z (nc -z itself is macOS/Linux only)
#   - Auto-detects pg_dump/python/ollama on PATH (no hardcoded Homebrew paths)
#   - No launchd KeepAlive throttling (sleep 30 on "already served" is macOS-only)
#   - Fallback .env/.venv paths use the repo root, not a hardcoded Downloads path
#
# LIMITATIONS (verified against this machine's state — edit if state changes):
#   - This script requires PostgreSQL on 127.0.0.1:5432 and a working venv python
#     to start uvicorn. On the current macOS checkout those are not available for
#     this verification, so the full startup path (Postgres wait -> ollama probe -
#     > uvicorn exec) is not executed here. The script logic is syntax-validated
#     clean under pwsh 7.6.6 and the shared .env regex is covered by
#     scripts/check_env_parser.ps1, but end-to-end startup is not run as part of
#     verification.
#   - PowerShell cannot exec, so uvicorn runs as a foreground child process; under
#     a supervisor/service manager the process tree differs from the bash exec
#     version (functionally equivalent for interactive use).

$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path -Parent $PSScriptRoot
$API_PORT = if ($env:SOC_API_PORT) { $env:SOC_API_PORT } else { "8000" }
$API_HOST = if ($env:SOC_API_HOST) { $env:SOC_API_HOST } else { "127.0.0.1" }
$OLLAMA_PORT = if ($env:OLLAMA_PORT) { $env:OLLAMA_PORT } else { "11434" }

# Config + virtualenv: prefer the canonical repo copy.
$ENV_FILE = if ($env:SOC_ENV_FILE) { $env:SOC_ENV_FILE } else { Join-Path $RepoRoot ".env" }
$VENV_PY = if ($env:SOC_VENV_PY) { $env:SOC_VENV_PY } else { Join-Path $RepoRoot ".venv\Scripts\python.exe" }

# Also try the Unix-style venv path (PowerShell Core on macOS/Linux)
if (-not (Test-Path $VENV_PY)) {
    $UnixVenv = Join-Path $RepoRoot ".venv/bin/python"
    if (Test-Path $UnixVenv) { $VENV_PY = $UnixVenv }
}

# Fall back to the original Downloads checkout where .env/.venv lived first
if (-not (Test-Path $ENV_FILE)) {
    $DownloadsEnv = Join-Path $HOME "Downloads/soc-plaform-main/.env"
    if (Test-Path $DownloadsEnv) { $ENV_FILE = $DownloadsEnv }
}
if (-not (Test-Path $VENV_PY)) {
    $DownloadsVenv = Join-Path $HOME "Downloads/soc-plaform-main/.venv/Scripts/python.exe"
    if (-not (Test-Path $DownloadsVenv)) {
        $DownloadsVenv = Join-Path $HOME "Downloads/soc-plaform-main/.venv/bin/python"
    }
    if (Test-Path $DownloadsVenv) { $VENV_PY = $DownloadsVenv }
}

$LOG_DIR = if ($env:SOC_LOG_DIR) { $env:SOC_LOG_DIR } else { Join-Path $RepoRoot "logs" }
New-Item -ItemType Directory -Path $LOG_DIR -Force | Out-Null

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    Add-Content -Path (Join-Path $LOG_DIR "soc-api.log") -Value $line
    Write-Host $line
}

function Test-Port {
    param([string]$Host, [int]$Port)
    try {
        $tcp = [System.Net.Sockets.TcpClient]::new()
        $tcp.Connect($Host, $Port)
        $tcp.Close()
        return $true
    } catch {
        return $false
    }
}

# 1) Wait up to 60s for PostgreSQL
Write-Host "Waiting for PostgreSQL on 127.0.0.1:5432..."
$pgReady = $false
for ($i = 1; $i -le 60; $i++) {
    if (Test-Port -Host "127.0.0.1" -Port 5432) { $pgReady = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $pgReady) {
    Write-Log "ERROR: Postgres not listening on 127.0.0.1:5432 after 60s"
    exit 1
}

# 2) Start Ollama if it is not already serving
if (-not (Test-Port -Host "127.0.0.1" -Port $OLLAMA_PORT)) {
    $ollamaBin = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if (-not $ollamaBin) {
        # Probe usual install locations (cross-platform)
        $candidates = @(
            "$env:LOCALAPPDATA\Ollama\ollama.exe",
            "$env:PROGRAMFILES\Ollama\ollama.exe",
            "/opt/homebrew/bin/ollama",
            "/usr/local/bin/ollama",
            "$HOME/Applications/Ollama.app/Contents/Resources/ollama",
            "/Applications/Ollama.app/Contents/Resources/ollama"
        )
        foreach ($c in $candidates) {
            if (Test-Path $c -PathType Leaf) { $ollamaBin = $c; break }
        }
    }
    if ($ollamaBin -and (Test-Path $ollamaBin -PathType Leaf)) {
        $proc = Start-Process -FilePath $ollamaBin -ArgumentList "serve" -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $LOG_DIR "ollama.log") -RedirectStandardError (Join-Path $LOG_DIR "ollama.log")
        Write-Log "started ollama serve (pid $($proc.Id))"
    } else {
        Write-Log "WARN: ollama binary not found; AI analysis will be unavailable"
    }
}

# 3) Start the API unless the port is already served (another instance won)
if (Test-Port -Host $API_HOST -Port $API_PORT) {
    Write-Log "API port $API_PORT already served; nothing to do"
    exit 0
}

if (-not (Test-Path $ENV_FILE)) {
    Write-Log "ERROR: no .env found (tried $ENV_FILE)"
    exit 1
}
if (-not (Test-Path $VENV_PY)) {
    Write-Log "ERROR: no venv python found (tried $VENV_PY)"
    exit 1
}

Write-Log "starting uvicorn on ${API_HOST}:${API_PORT}"
Set-Location $RepoRoot

# Load .env into the process environment, then exec uvicorn
$envVars = @{}
Get-Content $ENV_FILE | ForEach-Object {
    if ($_ -match '^\s*([^#=#\s][^=]*)=(.*)$') {
        $envVars[$Matches[1].Trim()] = $Matches[2].Trim()
    }
}
foreach ($kv in $envVars.GetEnumerator()) {
    Set-Item -Path "env:$($kv.Key)" -Value $kv.Value
}

# PowerShell can't exec, so we run uvicorn as a child process and keep the script alive
# (the bash version uses exec; here we mirror that by running it as the foreground process)
& $VENV_PY -m uvicorn --app-dir $RepoRoot api.main:app --host $API_HOST --port $API_PORT
