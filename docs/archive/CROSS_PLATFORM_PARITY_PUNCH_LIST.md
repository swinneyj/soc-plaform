# Cross-Platform Parity Punch List

*Grounded in the actual tree on `dev-dalton` as of the `scripts(pwsh): add cross-platform start/backup + env-parser check` commit. Status tags: ✅ present/verified, ⚠️ present but with a caveat, ❌ absent, 🟦 deliberate decision (not a gap).*

## What already works everywhere

| Capability | macOS | Linux | Windows | Notes |
|---|---|---|---|---|
| Python app code (`api/`, `db/`, `services/`, `tests/`) | ✅ | ✅ | ✅ | Pure Python; `psycopg[binary]` ships wheels for all three. CI matrix 3.14 + 3.9. |
| Docker Compose | ✅ (Desktop) | ✅ (Engine) | ✅ (Desktop) | Compose file is platform-neutral. `extra_hosts: host.docker.internal:host-gateway` is Linux-only (no-op on macOS/Windows Desktop). |
| `scripts/start_platform.ps1` | ✅ (pwsh) | ✅ (pwsh) | ✅ (pwsh 7+) | Full platform start: Docker compose postgres + api-service, shared-dump restore, sync, health check. No Windows-only cmdlets. Hardcoded Windows share path `Z:\PAX DNA SOC\...` as default `$SharedDumpDir` — override with `-SharedDumpDir` on macOS/Linux. |
| All other `.ps1` scripts | ✅ (pwsh) | ✅ (pwsh) | ✅ (pwsh 7+) | No Windows-only cmdlets in the SOC scripts (no `Get-Service`, no registry). |
| `.bat` wrappers | n/a | n/a | ✅ | Windows-only by design (double-click convenience). |
| `scripts/check_undefined_names.sh` + `check_no_merge_markers.sh` | ✅ | ✅ | ❌ no bash | Pure `pyflakes`/`grep`; trivially wrappable in pwsh if Windows CI gate needed. |
| `scripts/check_env_parser.ps1` (new) | ✅ | ✅ | ✅ | Repeatable pwsh harness for the shared `.env` regex. |
| `scripts/start_soc_api.ps1` (new) | ✅ | ✅ | ✅ (see caveat) | Standalone API daemon starter. |
| `scripts/backup_db.ps1` (new) | ✅ | ✅ | ✅ (see caveat) | Daily backup. |

## Remaining gaps, in priority order

### P1 — No scheduled backups on any platform (deliberate, off the table)

- **State:** there is **no scheduled backup on any platform**. The macOS launchd plist `scripts/local.soc-platform.backup.plist` (03:30 daily) exists on disk but is **deliberately not installed** (owner decision, Sept 30). Windows has no scheduler integration at all.
- **What exists instead:** ops rides along with platform starts — `scripts/start` refreshes the backup when the newest dump is < 20 h old, and a forced backup runs on demand. `scripts/backup_db.ps1` / `scripts/export_postgres_dump.ps1` can produce a dump manually when pg_dump/gzip/Docker are available, but there is no automation behind them.
- **This is a decision, not an open gap.** The punch list records it so it isn't re-opened as "the biggest real hole" or picked up as Phase 5 ops backlog. No owner, no scheduled backup on any platform, by choice.
- **If that decision ever changes:** anything that hits the shared Neon DB needs the pre-action confirmation protocol from `AGENTS.md` ("One shared production dataset"). `backup_db.ps1` reads `DATABASE_URL` from `.env` — if that's the Neon connection string, the dump hits shared prod data.

### P2 — `start_soc_api.ps1` on Windows: uvicorn "no exec" process-tree difference

- **State:** PowerShell cannot `exec` like bash. `start_soc_api.ps1` runs uvicorn as a foreground child process and stays alive as the parent. The bash `start_soc_api.sh` uses `exec` (replaces itself with uvicorn).
- **Impact:** for interactive/terminal use, identical. Under a Windows service manager / supervisor / parent that expects to replace the script process, the process tree differs (the script remains as a parent of uvicorn).
- **Severity:** low for human use; medium if someone wraps `start_soc_api.ps1` as a Windows service expecting the bash semantics.
- **Suggested owner:** only if/when someone actually wraps the API as a Windows service. Otherwise document and leave it.

### P3 — `start_soc_api.ps1` / `backup_db.ps1` end-to-end unexecuted on this machine

- **State:** on the current macOS checkout, neither `pg_dump` nor `gzip` is on PATH and Docker is not available. So `backup_db.ps1`'s dump → gzip → retention pipeline and `start_soc_api.ps1`'s full startup path (Postgres wait → ollama probe → uvicorn exec) cannot be executed here.
- **What IS verified:** pwsh 7.6.6 `Parser.ParseInput` clean on both; `scripts/check_env_parser.ps1` covers the shared `.env` regex (17 cases, all pass); pytest 391/391 (no Python changed).
- **Documented in-script:** both scripts now carry a `# LIMITATIONS` block stating this explicitly, with the instruction "edit if state changes."
- **Suggested owner:** the next person with pg_dump/gzip/Docker available, or a Windows machine. Run the scripts end to end and confirm the in-script LIMITATIONS block is still accurate (update or remove it).

### P4 — `start_soc_api.sh` / `backup_db.sh` still macOS/Linux-only (by design, but worth stating)

- **State:** both bash originals use `nc -z` (macOS/Linux only), `brew --prefix` (macOS/Linux Homebrew), and `#!/usr/bin/env bash`. They have no Windows equivalent.
- **Coverage:** `start_soc_api.ps1` is the cross-platform sibling for the API daemon start; `backup_db.ps1` is the cross-platform sibling for the backup. `start_platform.ps1` covers the full platform start on Windows.
- **Gaps that remain by design:**
  - No Windows equivalent of the macOS launchd API daemon (`local.soc-platform.api.plist` → `start_soc_api.sh`). On Windows you start the API via `start_platform.ps1` (container) or `start_soc_api.ps1` (standalone uvicorn). No Windows service wrapper for the standalone uvicorn path.
  - No Windows scheduled backup (see P1).
- **Suggested owner:** only if Windows parity becomes a real requirement. Otherwise state the boundary explicitly (partially done in the script headers) and leave it.

### P5 — `nc -z` footgun in `start_soc_api.sh` (portability, contained)

- **State:** `start_soc_api.sh` uses `nc -z` for port probing. `nc` is macOS/Linux only. The script is macOS-only anyway (launchd wrapper), so this is not a cross-platform bug — but it's a portability footgun if someone ports the script.
- **Mitigation already in tree:** `start_soc_api.ps1` uses `.NET TcpClient` instead, which works everywhere pwsh runs.
- **Suggested owner:** none required. The ps1 sibling already addresses it. Worth a comment in `start_soc_api.sh` header pointing to the ps1 for cross-platform use (low priority).

## Deliberate decisions (not gaps — listed so they aren't re-opened)

| Decision | Evidence | Decision date |
|---|---|---|
| No launchd/cron scheduler installed for backups or API | `docs/SETUP.md` (launchd/cron row); `docs/EXECUTION_PLAYBOOK.md` (launchd/cron row); `scripts/start` (Phase 5 ops note); `docs/DEVELOPMENT_PLAN.md` (Backups bullet) | Sept 30, 2026 |
| API boots on demand via `scripts/start` / Desktop icon, not launchd | `AGENTS.md` "Boot persistence" thread (✅ decided Sept 28: no auto-start) | Sept 28, 2026 |
| `host.docker.internal:host-gateway` in compose is Linux-only (no-op on macOS/Windows Desktop) | `docker-compose.yml` (extra_hosts block) | — |
| `.bat` files are Windows-only by design | — | — |
| `start_soc_api.sh`/`backup_db.sh` are macOS-only by design (launchd + bash + Homebrew) | — | — |

## Sources (so this list can be re-derived, not trusted on prose alone)

- `scripts/start_soc_api.ps1`, `scripts/backup_db.ps1`, `scripts/check_env_parser.ps1` — the three files added in the `scripts(pwsh)` commit.
- `scripts/start_platform.ps1` — the full platform starter (Windows coverage reference).
- `scripts/start_soc_api.sh`, `scripts/backup_db.sh` — the macOS bash originals.
- `scripts/local.soc-platform.api.plist`, `scripts/local.soc-platform.backup.plist` — the launchd plists (exist on disk, not installed by decision).
- `docker-compose.yml` — `extra_hosts` line.
- `docs/SETUP.md`, `docs/EXECUTION_PLAYBOOK.md`, `docs/DEVELOPMENT_PLAN.md`, `AGENTS.md` — the decision records.
- `scripts/start` — the ops-riding-backup note.

## How to use this list

- Treat P1 as the only actionable item if Windows parity matters at all. Everything else is either already addressed by the new ps1 scripts, already documented in-script, or a deliberate decision.
- Before acting on P1 or P3 in a way that touches the shared Neon DB: follow the `AGENTS.md` "One shared production dataset" protocol (confirm which DB you're on, get explicit user confirmation). `backup_db.ps1` reads `DATABASE_URL` from `.env` — if that's the Neon connection string, the dump hits shared prod data.
