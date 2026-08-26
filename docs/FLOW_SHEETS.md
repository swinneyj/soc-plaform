# SOC Flow Sheets

This file is for the "what do I do next?" question.

Use it when the repo feels too big, Git feels risky, or you are not sure which script is the right entry point.

## Core Rule

Do not try to remember everything.

Use this order:

1. Check state
2. Repair or start only what is needed
3. Validate the live stack
4. Sync only when upstream is stable
5. Push only from your own branch

## Four Main Scripts

### 1. Troubleshoot and decide

```powershell
.\scripts\troubleshoot_platform.ps1
```

Use this first.

What it does:
- checks Git state
- checks Docker and compose
- checks API health
- checks PostgreSQL port
- checks Ollama
- checks shared handoff files
- prints the next recommended command

### 2. Start the platform

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

Use this when the troubleshooter says the platform is down.

### 3. Sync the latest changes from main safely

```powershell
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

Use this instead of raw `git pull` when local edits exist.

### 4. Push your work on a non-main branch

```powershell
.\git-menu.ps1
```

Use this when you are ready to commit and push your own branch.

It blocks direct pushes from `main`.

## Flow 1: Daily Start

Use this when you open the repo and want to know whether you can work.

```powershell
.\scripts\troubleshoot_platform.ps1
```

If it says the stack is down:

```powershell
.\scripts\start_platform.ps1 -EnsureOllama -OpenBrowser
```

Then validate:

```powershell
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

If smoke test passes, the local app is ready.

## Flow 2: Something Feels Broken

Use this when PowerShell, Docker, or the app seems stuck.

```powershell
.\scripts\troubleshoot_platform.ps1 -ShowEvidence
```

Read the first `FAIL` item first.

Do not try to fix five things at once.

Work in this order:

1. Hard failures
2. Smoke test
3. Git warnings

## Flow 3: Coworker Is Still Pushing To Main

Use this when someone says they are still updating `origin/main`.

Do not pull yet.

Your flow is:

1. Keep local work local
2. Validate your current stack only
3. Wait for a clear "main is ready" message

Commands you can still run safely:

```powershell
.\scripts\troubleshoot_platform.ps1
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

Commands to avoid during that window:

```powershell
git pull
```

## Flow 4: Main Is Ready And You Need To Sync

Use this after the coworker says they are done pushing.

Step 1:

```powershell
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

Step 2:

```powershell
.\scripts\troubleshoot_platform.ps1
```

Step 3:

```powershell
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

If all of that passes, your new local baseline is trusted.

## Flow 5: Merge Conflict During Sync

Use this when the safe sync stops on a conflict.

Your flow is:

1. Resolve only the conflicted file or files
2. Stage those files
3. Complete the merge commit
4. Re-run troubleshoot and smoke test

Typical commands:

```powershell
git status
git add <resolved-files>
git commit
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

Do not reopen broad repo exploration in the middle of a conflict.

## Flow 6: Ready To Push Your Own Branch

Use this after local validation succeeds.

Step 1:

```powershell
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

Step 2:

```powershell
.\git-menu.ps1
```

That script will:
- stop direct pushes from `main`
- create a temporary branch
- commit your work
- push your branch

## Flow 7: Quick Decision Table

If the question is:

- "Is the app healthy?"
  Run `.\scripts\troubleshoot_platform.ps1 -RunSmokeTest`
- "Should I start the platform?"
  Run `.\scripts\troubleshoot_platform.ps1`
- "Should I pull main now?"
  Only after the coworker says upstream is ready
- "How do I pull safely?"
  Run `.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint`
- "How do I push without touching main?"
  Run `.\git-menu.ps1`

## Things To Avoid

- raw `git pull` when you have local edits
- pushing directly from `main`
- treating old container logs as the current truth without rechecking `/health`
- trying to fix Git, Docker, API, and database problems all at once
- skipping smoke tests after a sync or merge

## Recommended Reading Order

If you want to understand the repo better over time, read in this order:

1. `OPERATOR_CHEAT_SHEET.md`
2. `docs\FLOW_SHEETS.md`
3. `docs\RESTART.md`
4. `docs\CURRENT_OPERATING_MODEL.md`
5. `docs\REPO_MAP.md`