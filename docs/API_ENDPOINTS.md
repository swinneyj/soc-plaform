# API Endpoints

Every HTTP route the FastAPI service exposes, with its **canonical** spelling, its
**legacy `/api/db/*` alias** (where one exists), the HTTP method, and its auth
behavior.

- Source of truth: the `@router.*` decorators in [`api/routes/`](../api/routes/)
  and the alias rewriter `canonical_to_legacy_path` in [`api/main.py`](../api/main.py).
- Interactive docs (`/docs`, `/openapi.json`) are disabled unless `ENABLE_DOCS=1`
  (they are a recon convenience locally and an attack-surface map in production).

## Auth model (applies to every table below)

Driven by a single seam in [`api/auth.py`](../api/auth.py); `API_KEY` comes from
the environment.

| `API_KEY` state | Behavior |
| --- | --- |
| **Unset** (local dev default) | Everything is open, localhost exposure only. |
| **Set** (armed) | **Every mutating request** — `POST`/`PUT`/`PATCH`/`DELETE` — to any `/api/*` path is rejected with `401` unless it presents the key. `GET` requests and the static UI stay open. |

The key is accepted as either:

- the `X-API-Key` header (what the frontend's `web/utils/auth.js` stamps on every
  axios request), or
- an `?api_key=` query parameter (smoke-test convenience).

On top of the middleware gate, a handful of dangerous routes also pin the
`require_api_key` **dependency** as defense in depth; those are marked
**key (middleware + pinned)** below — they fail closed with `401` even if the
middleware were removed. Everything else mutating is
**key when armed (middleware)**.

> The auth middleware runs *outside* the path rewriter, so canonical and legacy
> spellings are gated identically.

## Path conventions

- **Canonical** resource paths (`/api/cases`, `/api/notables`, `/api/evidence`,
  `/api/analyses`) are preferred for new integrations (S13).
- **Legacy** `/api/db/*` spellings remain as aliases and are what the current
  frontend uses; they are not deprecated.
- The rewrite is a straight path alias at the ASGI layer — same handler, same
  payload, same status codes. Unknown subresources 404 identically under either
  spelling.

---

## System & health

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | — | `/health` | Open | Process liveness (no `/api/` prefix; outside the gate). |
| GET | — | `/api/health` | Open | Deploy smoke test: DB reachability + version. |
| GET | — | `/api/` | Open | API index banner. |
| GET | — | `/api/db/ollama/health` | Open | Local Ollama reachability + model list. |
| GET | — | `/api/db/stats` | Open | DB counts (cases, notables, evidence, states). |
| GET | — | `/api/db/operations` | Open | Operations dashboard aggregates. |
| GET | — | `/api/registry` | Open | Tool registry snapshot. |
| POST | — | `/api/registry/reload` | Key when armed (middleware) | Re-scan the Tools directory. |

## Cases (triage) — canonical family `/api/cases`

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/api/cases` | `/api/db/triage` | Open | List triage cases (supports `delete_case_id`, `delete_analysis` query params on this read). |
| GET | `/api/cases/{case_id}` | `/api/db/triage/{case_id}` | Open | Single case detail. |
| POST | `/api/cases/{case_id}/delete` | `/api/db/triage/{case_id}/delete` | Key when armed (middleware) | Delete a case (and optionally its analysis). |
| GET | `/api/cases/{case_id}/delete` | `/api/db/triage/{case_id}/delete` | Open | Delete via GET — kept for legacy links; prefer POST. |
| POST | `/api/cases/batch-delete` | `/api/db/triage/batch-delete` | Key when armed (middleware) | Delete many cases. |
| GET | `/api/cases/{case_id}/notable` | `/api/db/triage/{case_id}/notable` | Open | The source notable linked to the case. |
| GET | `/api/cases/{case_id}/investigation-state` | `/api/db/triage/{case_id}/investigation-state` | Open | Persisted loop state (status, confidence, timeline). |
| GET | `/api/cases/{case_id}/closure-readiness` | `/api/db/triage/{case_id}/closure-readiness` | Open | Gating evaluation; each blocker annotated with a `{stage, label}` routing action (S9). |

## Notables — canonical family `/api/notables`

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/api/notables` | `/api/db/notables` | Open | Recent pasted notables. |
| GET | `/api/notables/historical` | `/api/db/notables/historical` | Open | Historical (closed) pasted notables. |
| POST | `/api/notables/paste` | `/api/db/notables/paste` | Key when armed (middleware) | Save a pasted notable (parse pipeline + sanitization). |
| GET | `/api/notables/generate-fetch-spl` | `/api/db/notables/generate-fetch-spl` | Open | GET convenience wrapper for fetch-SPL generation (query params mirror the POST body). |
| POST | `/api/notables/generate-fetch-spl` | `/api/db/notables/generate-fetch-spl` | Key when armed (middleware) | Generate a Splunk fetch query from a notable description. |
| GET | `/api/notables/{event_id}` | `/api/db/notables/{event_id}` | Open | One pasted notable. |
| DELETE | `/api/notables/{event_id}` | `/api/db/notables/{event_id}` | Key when armed (middleware) | Delete a pasted notable. |
| POST | `/api/notables/{event_id}/delete` | `/api/db/notables/{event_id}/delete` | Key when armed (middleware) | Delete via POST (frontend uses this). |
| GET | `/api/notables/{event_id}/delete` | `/api/db/notables/{event_id}/delete` | Open | Delete via GET — kept for legacy links; prefer POST. |
| POST | `/api/notables/batch-delete` | `/api/db/notables/batch-delete` | Key when armed (middleware) | Delete many pasted notables. |
| POST | `/api/notables/{event_id}/promote` | `/api/db/notables/{event_id}/promote` | Key when armed (middleware) | Promote a pasted notable into a triage case. |
| POST | `/api/notables/backfill-closure-notes` | `/api/db/notables/backfill-closure-notes` | Key when armed (middleware) | Backfill closure notes for promoted notables. |

## Case evidence — canonical families `/api/cases/{id}/evidence` and `/api/evidence/{id}`

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | `/api/evidence/{case_id}` · `/api/cases/{case_id}/evidence` | `/api/db/triage/{case_id}/evidence` | Open | List saved evidence (optional `source_system` filter). |
| POST | `/api/evidence/{case_id}` · `/api/cases/{case_id}/evidence` | `/api/db/triage/{case_id}/evidence` | Key when armed (middleware) | Append a validated evidence batch (manual/`splunk_auto` saves). |
| DELETE | `/api/evidence/{case_id}/{evidence_id}` · `/api/cases/{case_id}/evidence/{evidence_id}` | `/api/db/triage/{case_id}/evidence/{evidence_id}` | Key when armed (middleware) | Delete one evidence item. |
| POST | `/api/evidence/{case_id}/{evidence_id}/delete` · `/api/cases/{case_id}/evidence/{evidence_id}/delete` | `/api/db/triage/{case_id}/evidence/{evidence_id}/delete` | Key when armed (middleware) | Delete one evidence item via POST. |
| POST | `/api/evidence/{case_id}/batch-delete` · `/api/cases/{case_id}/evidence/batch-delete` | `/api/db/triage/{case_id}/evidence/batch-delete` | Key when armed (middleware) | Delete many evidence items by id (frontend delete paths). |
| POST | `/api/evidence/{case_id}/delete-all` · `/api/cases/{case_id}/evidence/delete-all` | `/api/db/triage/{case_id}/evidence/delete-all` | Key when armed (middleware) | Delete every evidence item for the case. |

## AI analysis — canonical family `/api/analyses`

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| POST | `/api/analyses` | `/api/db/analyze` | Key when armed (middleware) | Run an analysis phase (initial or follow-up); returns queries, loop state, per-card verdicts. |

## Rules, supportive queries & placeholder aliases — legacy only

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | — | `/api/db/rules` | Open | Detection rule catalog. |
| GET | — | `/api/db/supportive-queries` | Open | Supportive queries (filter by `rule_id`). |
| POST | — | `/api/db/supportive-queries` | Key when armed (middleware) | Create a supportive query (includes Phase-2 promote). |
| PUT | — | `/api/db/supportive-queries/{query_id}` | Key when armed (middleware) | Update a supportive query. |
| DELETE | — | `/api/db/supportive-queries/{query_id}` | Key when armed (middleware) | Delete a supportive query. |
| POST | — | `/api/db/supportive-queries/draft` | Key when armed (middleware) | Draft supportive queries from a rule family. |
| GET | — | `/api/db/supportive-queries/status/{case_id}` | Open | Playbook availability for a case. |
| POST | — | `/api/db/supportive-queries/import-results` | Key when armed (middleware) | Import externally collected results as evidence. |
| GET | — | `/api/db/placeholder-aliases` | Open | Placeholder alias map. |
| GET | — | `/api/db/placeholder-aliases/suggestions` | Open | Alias suggestions for unresolved placeholders. |
| POST | — | `/api/db/placeholder-aliases` | Key when armed (middleware) | Create an alias. |
| PUT | — | `/api/db/placeholder-aliases/{alias_id}` | Key when armed (middleware) | Update an alias. |
| DELETE | — | `/api/db/placeholder-aliases/{alias_id}` | Key when armed (middleware) | Delete an alias. |

## Closure — legacy only

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| POST | — | `/api/db/closure-note` | Key when armed (middleware) | Generate the structured closure note (readiness-gated; `force_closure` override). |

Readiness evaluation lives under the cases family above
(`/api/cases/{id}/closure-readiness`).

## Splunk

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | — | `/api/splunk-boundary/status` | Open | Boundary latch status (read-only diagnostics). |
| POST | — | `/api/splunk-boundary/admit` | Key (middleware + pinned) | Admit a batch through the boundary latch. |
| DELETE | — | `/api/splunk-boundary/batches/{batch_id}` | Key (middleware + pinned) | Release an admitted batch. |
| POST | — | `/api/splunk/search-one` | Key (middleware + pinned) | Run one SPL against the configured backend and save `splunk_auto` evidence. |

## Tools, execution & reports

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| GET | — | `/api/tools` | Open | Registered tools (`ToolInfo[]`). |
| GET | — | `/api/tools/{tool_name}` | Open | One tool's metadata. |
| POST | — | `/api/tools/regression` | Key (middleware + pinned) | Run a tool regression suite. |
| POST | — | `/api/execute` | Key (middleware + pinned) | Queue a tool execution job. |
| GET | — | `/api/jobs` | Open | List jobs. |
| GET | — | `/api/jobs/{job_id}` | Open | One job's status/result. |
| DELETE | — | `/api/jobs/{job_id}` | Key (middleware + pinned) | Delete a job. |
| DELETE | — | `/api/jobs` | Key (middleware + pinned) | Clear all jobs. |
| GET | — | `/api/reports` | Open | List generated reports. |
| GET | — | `/api/reports/{report_name}` | Open | Fetch a report. |
| GET | — | `/api/tool-artifacts/{artifact_path}` | Open | Fetch a tool artifact file. |

## Code review

| Method | Canonical | Legacy alias | Auth | Purpose |
| --- | --- | --- | --- | --- |
| POST | — | `/api/code-review` | Key when armed (middleware) | Start a code review. |
| POST | — | `/api/code-review/sections` | Key when armed (middleware) | Review selected sections. |
| POST | — | `/api/code-review/fix` | Key when armed (middleware) | Apply an AI-suggested fix. |
| POST | — | `/api/code-review/zip` | Key when armed (middleware) | Review an uploaded archive. |
| GET | — | `/api/code-reviews` | Open | List saved reviews. |
| GET | — | `/api/code-reviews/{review_id}` | Open | One saved review. |

---

## Alias coverage summary (S13 rewriter)

Rewritten at the ASGI layer in [`api/main.py`](../api/main.py)
(`canonical_to_legacy_path` + `CanonicalPathRewriter`):

| Canonical prefix | Legacy target |
| --- | --- |
| `/api/cases` (+ any `/api/cases/{id}/…` tail) | `/api/db/triage` (+ same tail) |
| `/api/notables` (+ any tail) | `/api/db/notables` (+ same tail) |
| `/api/evidence/{case_id}` (+ tail) | `/api/db/triage/{case_id}/evidence` (+ tail) |
| `/api/analyses` | `/api/db/analyze` |

Everything else — health, rules, supportive queries, placeholder aliases,
closure-note, ollama, stats, operations, splunk, tools, jobs, reports, registry,
code-review — exists only at its documented spelling above.

Locked by `TestCanonicalRestPaths` in
[`tests/test_investigation_fixes.py`](../tests/test_investigation_fixes.py)
(mapping table, pass-through of non-canonical paths, and live alias/legacy
status parity through the real app).
