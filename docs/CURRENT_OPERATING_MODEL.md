# Current Operating Model

This is the current intended workflow for the canonical working copy.

## Canonical Repo

- Canonical repo: `SOC_Automation_Working_Fresh_20260825`
- Normal branch for pulling upstream changes: `main`
- Normal day-to-day start path on this machine: Desktop `Start_SOC.bat`

## Startup Modes

Use the right startup mode on purpose.

### Normal Resume

Use this for daily work.

- Desktop entry point: `Start_SOC.bat`
- Behavior:
  - if the API is already healthy, it just opens the browser
  - if the API is down, it starts the canonical repo in resume mode
  - resume mode skips shared dump restore to preserve local working state

Equivalent repo command:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser -SkipSharedDumpRestore
```

### Fresh Restore From Shared Handoff

Use this when you want a cautious refresh from the shared handoff dump.

- Desktop entry point: `Restore_SOC_From_Handoff.bat`
- The launcher requires typing `RESTORE` before it proceeds.
- This path exports current local state first, then starts the platform with the normal shared restore behavior.

Equivalent repo commands:

```powershell
.\scripts\stop_platform.ps1
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

### Force Restore From Shared Handoff

Use this only when you intentionally want to overwrite local runtime state and do not want to export current local state first.

- Desktop entry point: `Force_Restore_SOC_From_Handoff.bat`
- The launcher requires typing `FORCE` before it proceeds.

Equivalent repo commands:

```powershell
.\scripts\stop_platform.ps1 -SkipSharedDumpExport
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

## Git Workflow

Normal Git expectations:

- Pull upstream into local `main`
- Make changes on local `main`
- Pull before you start and push after local validation passes

The Git helper now enforces that rule:

- Option 1 pulls the latest upstream changes into local `main`
- Option 2 stages, commits, and pushes local `main`

Git helper file:

```text
git-menu.ps1
```

## Local DB Repro Workflow

Use this when you want to reproduce DB or analysis issues locally without refreshing from the shared dump.

Primary helper:

```powershell
.\scripts\start_local_db_repro.ps1 -OpenBrowser
```

What it does:

1. starts the platform in local resume mode if it is not already healthy
2. seeds synthetic triage test cases from `seed_test_cases.py`
3. seeds synthetic supportive results from `seed_supportive_results.py`

Useful flags:

```powershell
.\scripts\start_local_db_repro.ps1 -SkipPlatformStart
.\scripts\start_local_db_repro.ps1 -SkipTestCaseSeed
.\scripts\start_local_db_repro.ps1 -SkipSupportiveSeed
```

## Handoff Model

- Code, rules, supportive query definitions, and scripts move through Git
- Shared runtime state moves through the shared PostgreSQL dump handoff path
- Local debugging and repro work should stay local until intentionally exported
