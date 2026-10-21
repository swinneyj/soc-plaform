# SOC Operator Cheat Sheet

## Read This First If You Are New

If you need the step-by-step operator decision flows, read:

`docs/FLOW_SHEETS.md`

If you need the visual architecture and mental model map, read:

`docs/SYSTEM_VISUAL_MAP.md`

If you are a new developer (not just operating), start with [docs/NEW_DEVELOPER_ONBOARDING.md](docs/NEW_DEVELOPER_ONBOARDING.md) instead — it points back here once the guided path is done.

If you want the full visual dashboard in the browser, run:

`.\scripts\show_system_maps.ps1`

## Current Canonical Working Copy

Use this repo's **root folder** (where this file lives) for day-to-day work. On Windows with OneDrive it may be under `...\SOC\SOC_Automation_Working_Fresh_20260825`; on macOS it may be `~/Downloads/soc-plaform-main` — both are fine, just `cd` to the folder containing `docker-compose.yml`.

## Start

```powershell
# from the repo root (folder containing docker-compose.yml)
.\scripts\start_platform.ps1 -OpenBrowser
```

## Status

```powershell
.\scripts\status_platform.ps1
```

## Stop

```powershell
.\scripts\stop_platform.ps1
```

## Login & Roles (session auth — active since 2026-10-21)

`AUTH_MODE=session` is set in `.env`, so every `/api/*` call needs an
authenticated actor: a session cookie (log in through the UI's login modal)
or the armed `X-API-Key` (the UI stamps it automatically). The API-key gate is
unchanged — the key still satisfies every check as the admin machine actor.

- **Accounts** (shared Neon): `dalton` (admin), `analyst-demo` (analyst).
- **Passwords live in the macOS keychain, never in chat or files:**
  `scripts/secrets-keychain get SOC_ADMIN_PASSWORD` (or `SOC_ANALYST_PASSWORD`).
- **Manage accounts:** `eval "$(scripts/secrets-keychain inject)"` then
  `.venv314/bin/python scripts/manage_users.py list|create|set-role|set-password|deactivate`
  (create/set-password take `--password-env VAR` or a getpass prompt —
  never the password on argv).
- **Roles:** analyst = investigation work incl. case/evidence/notable/rule
  deletes (owner decision Oct 14, 2026); admin = tools/registry/jobs-delete/
  boundary admit. Matrix: `docs/SESSION_AUTH_PLAN.md` §3.
- **Audit:** every login, 401/403 and state change is a JSON line on stdout
  (see `tail -f /tmp/soc-api.log`, logger `soc.audit`).
- **Rollback:** remove the `AUTH_MODE=session` line from `.env`, then
  `scripts/start --stop && scripts/start`.

## Safe Pull

Check first:

```powershell
.\scripts\sync_upstream_safe.ps1 -CheckOnly
```

Pull with an automatic checkpoint if there are local edits:

```powershell
.\scripts\sync_upstream_safe.ps1 -AutoCheckpoint
```

## Troubleshoot And Smoke Test

Use this as the default decision tool before startup, sync, or push:

```powershell
.\scripts\troubleshoot_platform.ps1
```

Use this when the platform is already up and you want a live validation pass:

```powershell
.\scripts\troubleshoot_platform.ps1 -RunSmokeTest
```

## Push

Use the root launcher if you want a menu:

`git-menu.ps1`

Or do it manually:

```powershell
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