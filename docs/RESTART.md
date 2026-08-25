# Restart Guide

## After A Reboot

The clean supported way to bring the project back up is:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

Run that from the repo root.

What it does:
1. checks whether Docker is running and attempts to start Docker Desktop if it is not
2. optionally starts Ollama if it is not already listening on `127.0.0.1:11434`
3. starts the project PostgreSQL container, Redis, and the API service
4. waits for the API health endpoint to become ready
5. opens the browser

Commander is now optional and is not started automatically by the standard wrappers.

## Fastest GUI Path

You can also double-click:

```text
Start_SOC_Platform.bat
```

or:

```text
Launch_Commander.bat
```

Both wrappers now call the PowerShell startup script without auto-starting Commander.

## If You Want Commander

Run it manually from the repo root:

```powershell
python .\commander.py
```

## Stop The Platform

```powershell
.\scripts\stop_platform.ps1
```

To also stop Ollama if it is running:

```powershell
.\scripts\stop_platform.ps1 -StopOllama
```

## Quick Status Check

```powershell
.\scripts\status_platform.ps1
```

## If You Only Want The API Stack

```powershell
.\scripts\start_platform.ps1 -EnsureOllama
```

## If You Do Not Care About Ollama Yet

```powershell
.\scripts\start_platform.ps1 -OpenBrowser
```

The platform will still run. Only AI-analysis features will be unavailable until Ollama is started.

## Default Ports

- API: `8000`
- PostgreSQL container host port: `5433`
- Ollama: `11434`

If `8000` is already in use on the machine, override it:

```powershell
.\scripts\start_platform.ps1 -ApiPort 8010 -EnsureOllama -OpenBrowser
```

## Health Checks

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

## If The Machine Already Has Another Clone Running

Override the project PostgreSQL port and optionally the API port:

```powershell
.\scripts\start_platform.ps1 -PostgresHostPort 5434 -ApiPort 8006 -EnsureOllama -OpenBrowser
```