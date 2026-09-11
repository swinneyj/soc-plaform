# SOC Platform Chat Handoff

This document summarizes the investigation-workflow testing and development decisions from the prior chat so another user can continue without repeating the setup or experiments.

## Project and test environment

- Repository: `https://github.com/swinneyj/soc-plaform.git`
- Final promoted branch: `main`
- Development branch used during the work: `justin`
- Local API during tunnel testing: `http://127.0.0.1:8001`
- Docker API default: `http://127.0.0.1:8000`
- Local Ollama: `http://127.0.0.1:11434`
- Ollama model tested: `llama3.1:latest`
- Native PostgreSQL test database: `soc_platform` on port `5432`, role `mini`
- Docker PostgreSQL default: database `soc_platform`, role `soc_platform`, host port `5433`

Passwords were intentionally not included in this handoff.

## Original problem

The initial AI assessment appeared frozen after 30–111 seconds. The UI showed a timeout warning, but the Cancel button did not stop the request. The first version of the app had completed analysis in approximately 20 seconds.

The root causes and improvements addressed during testing included:

- Ollama requests taking too long or appearing stuck.
- Cancellation not invalidating the in-flight response correctly.
- The API being restarted on the wrong port or with the wrong Python command.
- Cloudflared pointing at the local API while the API was unavailable.
- PostgreSQL authentication failures caused by confusing the Mac account password with the PostgreSQL role password.
- Phase 2 evidence being saved but not clearly surfaced.
- Phase 2 analysis formatting raw Markdown instead of readable sections.
- Stage 5 returning to the same Phase 2 page instead of advancing the investigation.
- Blank, untouched follow-up cards being saved as evidence and creating false closure blockers.
- Closure rule names appearing as blank `(medium)` entries.

## API, tunnel, and browser setup

The local API was exposed with a quick Cloudflare tunnel:

```text
https://feed-inbox-prostate-project.trycloudflare.com
```

The Vercel preview used this API override:

```text
https://soc-plaform-git-justin-swinneyjs-projects.vercel.app/index.modular.html?api=https%3A%2F%2Ffeed-inbox-prostate-project.trycloudflare.com%2Fapi
```

Cloudflared continued pointing to:

```text
http://127.0.0.1:8001
```

The tunnel must remain running while the remote preview is used.

## API restart lessons

The correct local restart command is run from the project directory with the virtual environment available:

```bash
export DATABASE_URL='postgresql+psycopg://mini@localhost:5432/soc_platform'
./scripts/restart_api.sh
```

The password is omitted from the URL because `.pgpass` can provide it. The API must be restarted after backend changes. A frontend-only change requires a hard browser refresh, not an API restart.

Hard refresh on macOS:

```text
Cmd + Shift + R
```

The restart helper stops the process listening on port `8001`, starts the project virtualenv Python, and preserves the existing cloudflared target.

## PostgreSQL lessons

The Mac login password and PostgreSQL role password are separate credentials. The PostgreSQL role `mini` needed its own password. Authentication was tested with:

```bash
psql -h localhost -p 5432 -U mini -d soc_platform -c "select 1;"
```

For password-free local PostgreSQL client commands, the following entry was configured in `~/.pgpass`:

```text
localhost:5432:soc_platform:mini:YOUR_POSTGRES_PASSWORD
```

The file must have restrictive permissions:

```bash
chmod 600 ~/.pgpass
```

For Docker-based coworker setups, the corresponding host entry is:

```text
localhost:5433:soc_platform:soc_platform:YOUR_DOCKER_POSTGRES_PASSWORD
```

`.pgpass` is machine-local and must never be committed or shared.

## Fresh database dump

A fresh SQL dump was exported from the native PostgreSQL database using:

```bash
pg_dump \
  --no-owner \
  --no-privileges \
  -h localhost \
  -p 5432 \
  -U mini \
  -d soc_platform \
  -f local-backups/shared-db/current_soc_platform_dump.sql
```

The resulting file is approximately 400 KB and is Git-ignored. It should be transferred securely to another user rather than committed to Git.

The database does not need to be named after the owner. Each user should import the dump into their own local database named `soc_platform`.

## New-user setup

### Clone and configure

```bash
git clone https://github.com/swinneyj/soc-plaform.git
cd soc-plaform
git checkout main
cp .env.example .env
```

Set a private URL-safe PostgreSQL password in `.env`:

```env
POSTGRES_DB=soc_platform
POSTGRES_USER=soc_platform
POSTGRES_PASSWORD=YOUR_LOCAL_DB_PASSWORD
POSTGRES_HOST_PORT=5433
DATABASE_URL=postgresql+psycopg://soc_platform:YOUR_LOCAL_DB_PASSWORD@localhost:5433/soc_platform
COMPOSE_DATABASE_URL=postgresql+psycopg://soc_platform:YOUR_LOCAL_DB_PASSWORD@postgres:5432/soc_platform
OLLAMA_URL=http://host.docker.internal:11434
```

### Import and start

Copy the shared dump into:

```text
local-backups/shared-db/current_soc_platform_dump.sql
```

Start Ollama and install the model:

```bash
ollama serve
ollama pull llama3.1:latest
```

In another terminal, load the environment and start the platform:

```bash
set -a
source .env
set +a

./scripts/start_platform.sh
```

The startup script starts PostgreSQL and Redis, restores the shared dump, builds the API, and starts the API service.

Open:

```text
http://127.0.0.1:8000/index.modular.html
```

Verify:

```bash
curl http://127.0.0.1:8000/health
```

After the first import, avoid restoring the dump on every restart:

```bash
set -a
source .env
set +a
export SKIP_SHARED_DUMP_RESTORE=1
./scripts/start_platform.sh
```

Use `docker compose down` to stop while preserving data. Do not use `docker compose down -v` unless intentionally deleting the database volume.

## Investigation workflow implemented

The workflow now advances forward instead of repeatedly sending analysts backward:

```text
Evidence Collection
        ↓
Initial Assessment
        ↓
Phase 2 Follow-Up
        ↓ blockers remain
Phase 3 Follow-Up
        ↓ blockers remain
Phase 4 Follow-Up, and so on
        ↓
Final Verdict & Closure
```

Each follow-up phase has:

- Targeted SPL queries.
- Its own phase-specific evidence source, such as `phase3_manual` or `phase4_manual`.
- The prior analysis as context.
- Targeted unresolved inquiries.
- Durable evidence in the shared ledger.
- A phase-specific status and analysis display.

When existing approved queries have already been saved, later phases prefer an unused approved query instead of repeating the same cards. The PowerShell rule received a third approved query for encoded payload context.

## Sample case used

Case:

```text
TEST-POWERSHELL-1 - PowerShell In-Memory Execution (malicious)
```

Initial hypothesis:

```text
The incident involves a simulated in-memory PowerShell execution by an admin user, executing a suspicious encoded payload. The detection science and correlation logic indicate a medium severity alert.
```

Initial open inquiries:

1. What is the purpose of the encoded payload being executed?
2. Is this a known or unknown PowerShell script?
3. Are there any other suspicious PowerShell executions on the same host?

Initial and intermediate verdicts commonly showed:

```text
Disposition: Malicious
Confidence: 75%
```

## Synthetic evidence used for testing

The following was test data, not a claim about real telemetry. It was pasted into the Phase 3 encoded-payload query:

```text
_time                 host        user       Message
2026-09-10 23:40:18   WIN-APP-042 admin.jlee powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA...

ScriptBlockText:
Decoded payload launches an in-memory PowerShell command that uses
Net.WebClient.DownloadString against https://updates.corp.example/payload.ps1
and executes the returned content with IEX.

ParentImage:
C:\Windows\System32\svchost.exe

ParentCommandLine:
svchost.exe -k netsvcs

ProcessId:
4180
```

The intended test settings were:

```text
Result status: Success
Finding direction: Supports
Inquiry resolution: Resolves Inquiry
Inquiry: What is the purpose of the encoded payload being executed?
```

For the recent PowerShell activity query, the synthetic result was:

```text
_time                 host        user       script_blocks
2026-09-10 23:41:12   WIN-APP-042 admin.jlee Get-MpComputerStatus | Select-Object AMRunningMode.RealTimeProtectionEnabled
2026-09-10 23:42:03   WIN-APP-042 admin.jlee Invoke-WebRequest -Uri https://updates.corp.example/maintenance.ps1
2026-09-10 23:40:18   WIN-APP-042 admin.jlee powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA...

No additional suspicious in-memory PowerShell executions were identified on WIN-APP-042 during the review window.
```

Intended settings:

```text
Result status: Success
Finding direction: Refutes
Inquiry resolution: Resolves Inquiry
Inquiry: Are there any other suspicious PowerShell executions on the same host?
```

For the script-hash query, the synthetic result was:

```text
_time                 host        user       distinct_hashes  hashes
2026-09-10 23:40:18   WIN-APP-042 admin.jlee 1                7f3a9c2e1b4d6a8f9c0e11223344556677889900

The hash was not found in the authorized script baseline or known-good PowerShell catalog.
No matching maintenance ticket or approved software deployment was identified.
```

Intended settings:

```text
Result status: Success
Finding direction: Supports
Inquiry resolution: Resolves Inquiry
Inquiry: Is this a known or unknown PowerShell script?
```

## Resolution behavior

The evidence form separates two concepts:

- Finding direction: whether the evidence supports, refutes, or is neutral to the active verdict.
- Inquiry resolution: whether the evidence does not resolve, partially resolves, or resolves a specific open inquiry.

Resolved inquiries are removed from the active follow-up queue but remain durable in the evidence ledger. A **Show Resolved** control allows additional evidence to be added later.

If a query targets multiple inquiries, the analyst chooses the specific inquiry being resolved. This prevents one result from accidentally clearing unrelated blockers.

## Expected closure-ready result

After the synthetic Phase 3 and Phase 4 evidence was saved and analyzed, the case reached:

```text
READY FOR CLOSURE
Disposition: Malicious
Confidence: 95%
Substantive Corroborating Findings: 8
Active Investigation Blockers: 0
Unresolved Open Questions: 0
```

The next action is **Move to Closure Notes for this Case**.

## Git history of major workflow changes

The final promotion to `main` was merge commit `3dcb5cb`. Relevant commits include:

- `ac8dc7e` — Add forward-moving follow-up investigation phases.
- `cf23ead` — Allow editing follow-up SPL queries.
- `4e5ba15` — Track explicit inquiry resolution outcomes.
- `fa4d574` — Scope inquiry resolution to a selected question.
- `4b9e539` — Avoid saving untouched follow-up cards.
- `13176bc` — Reject blank successful evidence entries at the API layer.
- `020916a` — Show the inquiry selector for every follow-up query.
- `e01ac11` — Hide resolved follow-up inquiries while retaining history.
- `fede663` — Populate the closure rule selector from the rule catalog.
- `3dcb5cb` — Promote the tested branch to `origin/main` without force-pushing.

The working tree was clean after promotion. The database dump remains outside Git.

