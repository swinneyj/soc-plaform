# SOC Automation Platform

A local-first SOC workspace for evidence-driven triage, AI-assisted investigation, Splunk ES workflows, and structured case closure.

## What it does

- Runs local AI assessment with Ollama.
- Generates targeted, editable SPL follow-up queries.
- Adds evidence to a durable phase-aware ledger.
- Tracks inquiry resolution and closure blockers.
- Creates Phase 3+ follow-ups only when evidence requires them.
- Produces a final verdict and structured closure note.
- Includes Tools, Database, Jobs, Reports, and Code Review features.

Case state, analysis results, inquiry resolutions, and evidence history are stored in PostgreSQL so work can resume after a restart.

## Notable data source

Operational notables originate in **Splunk Enterprise Security (Splunk ES)**. Authorized notable or event data is ingested, normalized for local triage, and persisted in PostgreSQL for investigation history and review.

The Database tab is a local operational view of data received from Splunk ES, not the authoritative Splunk ES data store. Keep the originating Splunk search, notable ID, time range, and analyst context with the case when available.

Source references:

- [ingest_notables.py](ingest_notables.py)
- [data_source_catalog.json](data_source_catalog.json)
- [Database and API map](docs/API_DB_VISUAL_MAP.md)

## Architecture

| Layer | Technology | Purpose |
|---|---|---|
| Web UI | HTML, CSS, JavaScript | Analyst workspace |
| API | FastAPI | Case and workflow routes |
| Database | PostgreSQL | Durable investigation state |
| AI | Ollama | Local analysis and code review |
| Detection | Splunk ES / SPL | Notable context and validation |
| Runtime | Docker Compose or native services | Repeatable environments |

## Quick start

~~~bash
cp .env.example .env
ollama serve
ollama pull llama3.1:latest
./scripts/start_platform.sh
~~~

Open http://127.0.0.1:8000/index.modular.html and verify:

~~~bash
curl http://127.0.0.1:8000/health
~~~

The default Docker database host port is 5433. Keep credentials in .env or a machine-local credential store; never commit them.

For native API testing:

~~~bash
export DATABASE_URL='postgresql+psycopg://<db-user>:<db-password>@localhost:5432/soc_platform'
./scripts/restart_api.sh
~~~

## Running the tests

The regression suite (46 tests, no database or Ollama required) guards the evidence ledger, closure gate, model resolution, and Phase 3+ query generation:

~~~bash
.venv/bin/python -m pytest
~~~

**Before adopting any external snapshot or history rewrite, run this first** — it fails in under a second if the regression fixes have been overwritten.

## Investigation workflow

~~~text
Evidence Collection → Initial Assessment → Phase 2 Follow-Up
       → Phase 3+ only when blockers remain
       → Final Verdict & Closure → Structured Closure Notes
~~~

Each follow-up phase has targeted queries, a phase-specific evidence source, inquiry targets, analysis, and audit history. Resolved inquiries leave the active queue but remain available for additional validation.

## Repository map

- api/ — FastAPI routes and orchestration
- db/ — SQLAlchemy models and database wiring
- services/ — Ollama integrations
- web/ — browser dashboard
- scripts/ — startup and operational helpers
- Tools/ — registered SOC tools
- docs/ — operator and architecture references
- Data/Reports/ — generated reports

Start with [RUNBOOK.md](RUNBOOK.md), [OPERATOR_CHEAT_SHEET.md](OPERATOR_CHEAT_SHEET.md), [docs/REPO_MAP.md](docs/REPO_MAP.md), and [multi-user-workflow.md](multi-user-workflow.md).

## Security

- Use authorized Splunk ES data only.
- Keep passwords, tokens, keys, and private telemetry out of Git.
- Minimize sensitive content in pasted evidence.
- Use local PostgreSQL credentials or .pgpass; never hard-code secrets.
- Synthetic fixtures are for development only, not production evidence.
