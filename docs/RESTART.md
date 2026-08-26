# Restart Guide

## After A Reboot

The clean supported way to bring the project back up is:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

Run that from the repo root.

For the current machine-level operating model, also see `docs/CURRENT_OPERATING_MODEL.md`.

What it does:
1. checks whether Docker is running and attempts to start Docker Desktop if it is not
2. optionally starts Ollama if it is not already listening on `127.0.0.1:11434`
3. acquires a simple share lock in the handoff folder
4. starts the project PostgreSQL container and Redis
5. restores the latest shared dump from the handoff folder when present
6. syncs repo-managed shared logic into the local database
7. starts the API service and waits for health
8. opens the browser

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

On this machine, the cleaner Desktop-level wrappers are:

```text
..\..\Start_SOC.bat
..\..\Restore_SOC_From_Handoff.bat
..\..\Force_Restore_SOC_From_Handoff.bat
```

Use `Start_SOC.bat` for normal resume behavior.
Use `Restore_SOC_From_Handoff.bat` when you want to export current local runtime state first and then refresh from the shared handoff dump.
Use `Force_Restore_SOC_From_Handoff.bat` only when you intentionally want to overwrite local runtime state without exporting it first.

## If You Want Commander

Run it manually from the repo root:

```powershell
python .\commander.py
```

## Stop The Platform

```powershell
.\scripts\stop_platform.ps1
```

By default this exports the current local PostgreSQL state back to the shared handoff dump before stopping containers.

The stop flow also refreshes a metadata file beside the dump so users can see:
- who exported it
- when it was exported
- which machine exported it
- the dump file size

To also stop Ollama if it is running:

```powershell
.\scripts\stop_platform.ps1 -StopOllama
```

## Quick Status Check

```powershell
.\scripts\status_platform.ps1
```

This also reports whether the shared handoff lock file exists and who currently holds it.

It also reports the latest shared dump metadata when present.

## If You Only Want The API Stack

```powershell
.\scripts\start_platform.ps1 -EnsureOllama
```

## Daily Resume Mode

For local day-to-day work where you want to preserve local runtime state, start with:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser -SkipSharedDumpRestore
```

That is the behavior used by the Desktop `Start_SOC.bat` wrapper.

## If You Do Not Care About Ollama Yet

```powershell
.\scripts\start_platform.ps1 -OpenBrowser
```

The platform will still run. Only AI-analysis features will be unavailable until Ollama is started.

## If `ollama pull` Is Reset By The Network

Documented fallback path only, not currently kept active in the repo:

1. Use PowerShell or .NET `HttpClient` to download the model weights directly, because that transport worked on this machine even when `ollama pull` and `curl` were reset by the network policy.
2. Save the weights locally as a GGUF file.
3. Create a `Modelfile` whose `FROM` line points at that local GGUF path.
4. Run `ollama create <local-model-name> -f <Modelfile>` to register the model with the local Ollama service.

This worked in testing with a small `llama3.2:1b` model, but the model and helper script were intentionally removed afterward.

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

## Local DB Repro

To prepare a local-only DB repro environment without re-restoring from the shared dump:

```powershell
.\scripts\start_local_db_repro.ps1 -OpenBrowser
```

That helper starts the platform in resume mode if needed, then seeds synthetic triage and supportive test data for local testing.