# Route / Role Matrix (SEC-02)

**Status:** inventory + **proposal**. No enforcement was changed. The owner
decided on 2026-10-23 to review the matrix before any route gains a role gate,
because one recorded decision (Oct 14: "analyst deletes stay") points the
opposite way from this proposal for seven routes — see *Decisions needed*.

## How this file is produced

`scripts/route_matrix.py` reads every route from the router modules, resolves
each route's dependency tree (API-key dependency, role dependency and **which**
role, read from the closure), classifies it, and prints the table below. The
doc carries the output between markers so drift is detectable:

```bash
python scripts/route_matrix.py            # print the matrix
python scripts/route_matrix.py --check    # exit 1 when this file is stale
```

Two implementation notes that matter for trusting the numbers:

* FastAPI 0.146 stores included routers as `_IncludedRouter` wrappers with no
  `path`/`methods`, so walking `app.routes` reports **zero** routes. The script
  reads the routers' own `APIRoute` objects instead.
* Completeness is cross-checked against `app.openapi()`: the served method+path
  pairs must equal the listed ones (74 = 74 below). Starlette path converters
  are normalized for the comparison, since OpenAPI renders
  `{artifact_path:path}` as `{artifact_path}`.

## What is enforced today

| | Count | Detail |
|---|---|---|
| Routes | **74** | across 12 feature routers + `system` |
| Mutating (POST/PUT/PATCH/DELETE or delete-shaped GET) | **39** | |
| Read-only | **35** | |
| Carry a `require_api_key` dependency | **9** | `POST /api/execute`, `DELETE /api/jobs`, `DELETE /api/jobs/{job_id}`, `POST /api/splunk-boundary/admit`, `DELETE /api/splunk-boundary/batches/{batch_id}`, `GET /api/splunk/saved-searches`, `POST /api/splunk/search-one`, `GET /api/splunk/search-status`, `POST /api/tools/regression` |
| Carry a `require_role` dependency | **10** | `POST /api/execute`, `GET/DELETE /api/jobs`, `GET /api/jobs/{job_id}`, `GET /api/registry`, `POST /api/registry/reload`, `POST /api/splunk-boundary/admit`, `DELETE /api/splunk-boundary/batches/{batch_id}`, `POST /api/tools/regression`, `GET /api/tools`, `GET /api/tools/{tool_name}` |

What that means in each mode:

* **API-key mode (flag off)** — all 39 mutations need the key; **reads are open**
  by design (the key is a deployment credential, not a user).
* **Session mode** — every `/api/*` request needs an actor (reads included);
  mutations additionally need `X-CSRF-Token`.
* **Proxy mode (SEC-01)** — same read+write gate as session mode, identity from
  the trusted header.
* **Role separation** — covers **10 of 74 routes (14 %)**: the ten above.

So "read vs write" exists; "analyst vs admin" mostly does not.

## Proposed policy

Three roles, assigned by function rather than by HTTP verb:

* **admin (20 routes)** — machine/ops surfaces and anything destructive:
  * tool catalog + execution + registry: `GET /api/tools`, `GET /api/tools/{tool_name}`,
    `POST /api/execute`, `POST /api/tools/regression`, `GET /api/registry`,
    `POST /api/registry/reload`
  * job control: `GET /api/jobs`, `GET /api/jobs/{job_id}`, `DELETE /api/jobs`,
    `DELETE /api/jobs/{job_id}`
  * Splunk boundary (ingest/release/purge + its status): `POST /api/splunk-boundary/admit`,
    `DELETE /api/splunk-boundary/batches/{batch_id}`, `GET /api/splunk-boundary/status`
  * destructive case/notable/evidence paths: `POST /api/db/triage/{case_id}/delete`,
    `POST /api/db/triage/batch-delete`, `POST /api/db/triage/{case_id}/evidence/delete-all`,
    `POST /api/db/triage/{case_id}/evidence/batch-delete`,
    `POST /api/db/triage/{case_id}/evidence/{evidence_id}/delete`,
    `POST /api/db/notables/{event_id}/delete`, `POST /api/db/notables/batch-delete`
* **analyst (50 routes)** — the investigation flow: cases, notables, evidence,
  analyses, closure, rules/playbooks, reports, read-only Splunk search.
* **public (4 routes)** — `/health`, `/api/health`, `/api/auth/login`,
  `/api/auth/session` (must answer before login; `/api/auth/*` is exempt in
  every mode).

Note the asymmetry this creates: analyst-visible routes keep working exactly as
today for an **API key** caller (the key resolves as the admin machine actor),
so enforcement only ever restricts *session/proxy users*, never automation.

## Decisions needed before enforcement

1. **Deletes (7 routes).** This proposal makes case/notable/evidence deletion
   admin-only. The recorded **Oct 14 owner decision** is "analyst deletes stay".
   Both cannot hold. Which wins?
2. **Tool catalog reads (2 routes).** `GET /api/tools` and `GET /api/tools/{tool_name}`
   are metadata, already admin-gated — keep them admin, or open the catalog to
   analysts while keeping execution admin?
3. **`GET /api/jobs` / `GET /api/jobs/{job_id}`.** Admin, or may an analyst see
   job status? (Today both are admin-gated, so this is a *relaxation* question.)
4. **`GET /api/splunk-boundary/status`.** Ops monitoring surface, proposed admin;
   should an analyst see ingest health?
5. **Read split on the other 50.** Leave them analyst (proposed) or require
   admin for rules/playbook *writes* (`POST/PUT/DELETE /api/db/placeholder-aliases*`,
   `/api/db/supportive-queries*`) — i.e. is playbook authoring an analyst task?

## Enforcement options (pick one when 1–5 are answered)

| Option | Shape | Cost | Risk |
|---|---|---|---|
| **A. Dependency-only (recommended)** | add `Depends(require_role("admin"))` to the approved admin routes | one line per route, no handler change, `require_role` already binds in session+proxy mode | misses any future route unless a drift test exists |
| B. Central route/role table | one mapping in middleware, evaluated before routing | one place to read/review | path matching must survive the canonical-path rewriter |
| C. Write-only enforcement | gate the 39 mutations, leave reads open | smallest | leaves "which analyst can read case X" unanswered |

Option A plus a test that asserts *the set of role-gated routes equals the
matrix's admin set* closes the drift hole cheaply, and is what this matrix is
built to support.

## Not covered here

Per-case ownership (analyst A vs analyst B on the same case), row-level ACLs,
per-role rate limits, and the UI's role-based affordances (the UI already hides
admin controls; that is cosmetic, not a control).

## The matrix

<!-- BEGIN GENERATED ROUTE MATRIX -->
| # | Method | Path | Mutating | Existing key dep | Existing role dep | Proposed role |
|---|---|---|---|---|---|---|
| 1 | `GET` | `/api/` | no | - | - | **analyst** |
| 2 | `POST` | `/api/auth/login` | yes | - | - | **public** |
| 3 | `POST` | `/api/auth/logout` | yes | - | - | **analyst** |
| 4 | `GET` | `/api/auth/session` | no | - | - | **public** |
| 5 | `POST` | `/api/code-review` | yes | - | - | **analyst** |
| 6 | `POST` | `/api/code-review/fix` | yes | - | - | **analyst** |
| 7 | `POST` | `/api/code-review/sections` | yes | - | - | **analyst** |
| 8 | `POST` | `/api/code-review/zip` | yes | - | - | **analyst** |
| 9 | `GET` | `/api/code-reviews` | no | - | - | **analyst** |
| 10 | `GET` | `/api/code-reviews/{review_id}` | no | - | - | **analyst** |
| 11 | `POST` | `/api/db/analyze` | yes | - | - | **analyst** |
| 12 | `POST` | `/api/db/closure-note` | yes | - | - | **analyst** |
| 13 | `GET` | `/api/db/notables` | no | - | - | **analyst** |
| 14 | `POST` | `/api/db/notables/backfill-closure-notes` | yes | - | - | **analyst** |
| 15 | `POST` | `/api/db/notables/batch-delete` | yes | - | - | **admin** |
| 16 | `GET` | `/api/db/notables/generate-fetch-spl` | no | - | - | **analyst** |
| 17 | `POST` | `/api/db/notables/generate-fetch-spl` | yes | - | - | **analyst** |
| 18 | `GET` | `/api/db/notables/historical` | no | - | - | **analyst** |
| 19 | `POST` | `/api/db/notables/paste` | yes | - | - | **analyst** |
| 20 | `DELETE` | `/api/db/notables/{event_id}` | yes | - | - | **analyst** |
| 21 | `GET` | `/api/db/notables/{event_id}` | no | - | - | **analyst** |
| 22 | `POST` | `/api/db/notables/{event_id}/delete` | yes | - | - | **admin** |
| 23 | `POST` | `/api/db/notables/{event_id}/promote` | yes | - | - | **analyst** |
| 24 | `GET` | `/api/db/ollama/health` | no | - | - | **analyst** |
| 25 | `GET` | `/api/db/operations` | no | - | - | **analyst** |
| 26 | `GET` | `/api/db/placeholder-aliases` | no | - | - | **analyst** |
| 27 | `POST` | `/api/db/placeholder-aliases` | yes | - | - | **analyst** |
| 28 | `GET` | `/api/db/placeholder-aliases/suggestions` | no | - | - | **analyst** |
| 29 | `DELETE` | `/api/db/placeholder-aliases/{alias_id}` | yes | - | - | **analyst** |
| 30 | `PUT` | `/api/db/placeholder-aliases/{alias_id}` | yes | - | - | **analyst** |
| 31 | `GET` | `/api/db/rules` | no | - | - | **analyst** |
| 32 | `GET` | `/api/db/stats` | no | - | - | **analyst** |
| 33 | `GET` | `/api/db/supportive-queries` | no | - | - | **analyst** |
| 34 | `POST` | `/api/db/supportive-queries` | yes | - | - | **analyst** |
| 35 | `POST` | `/api/db/supportive-queries/draft` | yes | - | - | **analyst** |
| 36 | `POST` | `/api/db/supportive-queries/import-results` | yes | - | - | **analyst** |
| 37 | `GET` | `/api/db/supportive-queries/status/{case_id}` | no | - | - | **analyst** |
| 38 | `DELETE` | `/api/db/supportive-queries/{query_id}` | yes | - | - | **analyst** |
| 39 | `PUT` | `/api/db/supportive-queries/{query_id}` | yes | - | - | **analyst** |
| 40 | `GET` | `/api/db/triage` | no | - | - | **analyst** |
| 41 | `POST` | `/api/db/triage/batch-delete` | yes | - | - | **admin** |
| 42 | `GET` | `/api/db/triage/{case_id}` | no | - | - | **analyst** |
| 43 | `PUT` | `/api/db/triage/{case_id}/analysis-draft` | yes | - | - | **analyst** |
| 44 | `GET` | `/api/db/triage/{case_id}/closure-readiness` | no | - | - | **analyst** |
| 45 | `POST` | `/api/db/triage/{case_id}/delete` | yes | - | - | **admin** |
| 46 | `GET` | `/api/db/triage/{case_id}/evidence` | no | - | - | **analyst** |
| 47 | `POST` | `/api/db/triage/{case_id}/evidence` | yes | - | - | **analyst** |
| 48 | `POST` | `/api/db/triage/{case_id}/evidence/batch-delete` | yes | - | - | **admin** |
| 49 | `POST` | `/api/db/triage/{case_id}/evidence/delete-all` | yes | - | - | **admin** |
| 50 | `DELETE` | `/api/db/triage/{case_id}/evidence/{evidence_id}` | yes | - | - | **analyst** |
| 51 | `POST` | `/api/db/triage/{case_id}/evidence/{evidence_id}/delete` | yes | - | - | **admin** |
| 52 | `GET` | `/api/db/triage/{case_id}/investigation-state` | no | - | - | **analyst** |
| 53 | `GET` | `/api/db/triage/{case_id}/notable` | no | - | - | **analyst** |
| 54 | `POST` | `/api/execute` | yes | yes | admin | **admin** |
| 55 | `GET` | `/api/health` | no | - | - | **public** |
| 56 | `DELETE` | `/api/jobs` | yes | yes | admin | **admin** |
| 57 | `GET` | `/api/jobs` | no | - | - | **admin** |
| 58 | `DELETE` | `/api/jobs/{job_id}` | yes | yes | admin | **admin** |
| 59 | `GET` | `/api/jobs/{job_id}` | no | - | - | **admin** |
| 60 | `GET` | `/api/registry` | no | - | admin | **admin** |
| 61 | `POST` | `/api/registry/reload` | yes | - | admin | **admin** |
| 62 | `GET` | `/api/reports` | no | - | - | **analyst** |
| 63 | `GET` | `/api/reports/{report_name}` | no | - | - | **analyst** |
| 64 | `POST` | `/api/splunk-boundary/admit` | yes | yes | admin | **admin** |
| 65 | `DELETE` | `/api/splunk-boundary/batches/{batch_id}` | yes | yes | admin | **admin** |
| 66 | `GET` | `/api/splunk-boundary/status` | no | - | - | **admin** |
| 67 | `GET` | `/api/splunk/saved-searches` | no | yes | - | **analyst** |
| 68 | `POST` | `/api/splunk/search-one` | yes | yes | - | **analyst** |
| 69 | `GET` | `/api/splunk/search-status` | no | yes | - | **analyst** |
| 70 | `GET` | `/api/tool-artifacts/{artifact_path:path}` | no | - | - | **analyst** |
| 71 | `GET` | `/api/tools` | no | - | admin | **admin** |
| 72 | `POST` | `/api/tools/regression` | yes | yes | admin | **admin** |
| 73 | `GET` | `/api/tools/{tool_name}` | no | - | admin | **admin** |
| 74 | `GET` | `/health` | no | - | - | **public** |

**74 routes** — 20 proposed **admin**, 50 **analyst**, 4 **public**.

*Completeness check:* `app.openapi()` serves **74** method+path pairs, this matrix lists **74** — match.
<!-- END GENERATED ROUTE MATRIX -->
