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

- **Accounts:** `soc-admin` (admin) and `soc-analyst` (analyst) — role-named,
  not person-named. They live in **local Postgres** (the primary since Oct 23,
  2026); the shared Neon mirror only has the older accounts when you launch with
  `--shared`. Check the launcher's `db target:` line first.
- **You never type a password.** Two ways in:
  - **In the UI:** the bottom-right **role switcher** (`web/utils/dev_login.js`)
    shows the current role and switches with one click. It exists only when the
    API runs with `SOC_DEV_LOGIN=1` (set in `.env` for local work); switching
    works because the page already carries the API key, which the server treats
    as the admin machine actor — so it can only ever hand out a *lesser*
    session. An analyst session cannot switch back; the key can.
  - **From a terminal:** `scripts/login soc-analyst` mints a session and writes a
    cookie jar to `~/.soc/sessions/<account>.cookies`, so
    `curl -b ~/.soc/sessions/soc-analyst.cookies http://127.0.0.1:8000/api/tools`
    runs as that role. `--show-csrf` prints the token if you need a mutation.
- **Passwords still exist** (random, in the macOS keychain as
  `SOC_ADMIN_PASSWORD` / `SOC_ANALYST_PASSWORD`) for a real
  `POST /api/auth/login` when `SOC_DEV_LOGIN` is off. Never printed, never in
  argv: `scripts/login` reads them itself.
- **A key-only caller sees a working UI.** `GET /api/auth/session` answers 200
  for the API key (`user: "api-key"`, role admin) instead of 401 — before this
  fix the UI fell into a login modal that could never succeed on a database
  with no accounts.
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
- **Postgres (primary):** `127.0.0.1:5432`, database `soc_platform`, user `soc_platform`
  (Homebrew `postgresql@16`; the launchers start it if it is down)
- Shared Neon mirror: opt-in only — `scripts/start --shared` (`docs/LOCAL_FIRST_DATABASE.md`)
- Compose container host port: `5433` (Docker path only — not the primary)
- Authoritative answer, any time: `scripts/dev --check` → `db target:`

## Shared Handoff Notes

- Git contains code, rules, supportive SPL, templates, and scripts.
- Git does not contain the live DB contents.
- Current DB handoff is through the shared `current_soc_platform_dump.sql` file.
- Start/stop only hold the shared lock during restore/export windows, not for the whole runtime.