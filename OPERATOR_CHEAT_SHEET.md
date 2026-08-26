# SOC Operator Cheat Sheet

## Read This First If You Are New

If you need the step-by-step operator decision flows, read:

`docs\FLOW_SHEETS.md`

If you need the visual architecture and mental model map, read:

`docs\SYSTEM_VISUAL_MAP.md`

If you want the full visual dashboard in the browser, run:

`.\scripts\show_system_maps.ps1`

## Current Canonical Working Copy

Use this folder for day-to-day work:

`C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825`

## Start

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\start_platform.ps1 -OpenBrowser
```

## Status

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\status_platform.ps1
```

## Stop

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\stop_platform.ps1
```

## Safe Pull

Check first:

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\sync_upstream_safe.ps1 -CheckOnly
```

Pull with an automatic checkpoint if there are local edits:

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

## Troubleshoot And Smoke Test

Use this as the default decision tool before startup, sync, or push:

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\troubleshoot_platform.ps1
```

Use this when the platform is already up and you want a live validation pass:

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

## Push

Use the root launcher if you want a menu:

`Sync_To_Git.bat`

Or do it manually:

```powershell
Set-Location "C:\Users\dalton.lewis\OneDrive - US Navy-flankspeed\Desktop\SOC\SOC_Automation_Working_Fresh_20260825"
git add -A
git commit -m "Describe your change"
git push origin HEAD
```

## Health URLs

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/api/health`
- `http://127.0.0.1:8000/api/db/stats`
- `http://127.0.0.1:8000/api/db/triage`
- `http://127.0.0.1:8000/docs`

## Database Defaults

- API: `http://127.0.0.1:8000`
- Postgres host port: `5433`
- Database: `soc_platform`
- User: `soc_platform`

## Shared Handoff Notes

- Git contains code, rules, supportive SPL, templates, and scripts.
- Git does not contain the live DB contents.
- Current DB handoff is through the shared `current_soc_platform_dump.sql` file.
- Start/stop only hold the shared lock during restore/export windows, not for the whole runtime.