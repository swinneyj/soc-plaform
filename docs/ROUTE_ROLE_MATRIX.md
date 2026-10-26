# Route / Role Matrix (SEC-02)

**Status:** **enforced** (SEC-02, 2026-10-26 work order + HIGH-01 Tier-1
2026-10-26). All 70 authenticated routes carry `require_role`; the 5 public
routes carry none. Owner answers Q1–Q5 applied, plus the 3 system-
observability reads (`/api/db/stats`, `/api/db/operations`,
`/api/db/ollama/health`) are admin per HIGH-01 (system-config routes require
admin) — 20 admin / 50 analyst. `tests/test_route_matrix.py` asserts the
admin set and 403s for analyst-on-admin.

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
  pairs must equal the listed ones (75 = 75 below). Starlette path converters
  are normalized for the comparison, since OpenAPI renders
  `{artifact_path:path}` as `{artifact_path}`.

## What is enforced today

| | Count | Detail |
|---|---|---|
| Routes | **75** | across 12 feature routers + `system` |
| Mutating (POST/PUT/PATCH/DELETE or delete-shaped GET) | **40** | |
| Read-only | **35** | |
| Carry a `require_api_key` dependency | **9** | `POST /api/execute`, `DELETE /api/jobs`, `DELETE /api/jobs/{job_id}`, `POST /api/splunk-boundary/admit`, `DELETE /api/splunk-boundary/batches/{batch_id}`, `GET /api/splunk/saved-searches`, `POST /api/splunk/search-one`, `GET /api/splunk/search-status`, `POST /api/tools/regression` |
| Carry a `require_role` dependency | **70** | all authenticated routes (20 admin + 50 analyst); the 5 public routes carry none |

What that means in each mode:

* **API-key mode (flag off)** — `require_role` is a no-op; all 39 mutations need the key; **reads are open**
  by design (the key is a deployment credential, not a user).
* **Session mode** — every `/api/*` request needs an actor (reads included);
  mutations additionally need `X-CSRF-Token`; analyst actors 403 on the 20 admin routes.
* **Proxy mode (SEC-01/01B)** — same read+write gate as session mode, identity from
  the trusted header (bare id or X.509 DN).
* **Role separation** — covers **70 of 75 routes (93 %)**: 20 admin, 50 analyst.

## Enforced policy (Q1–Q5 answers applied)

Three roles, assigned by function rather than by HTTP verb:

* **admin (20 routes)** — machine/ops surfaces and anything destructive:
  * system observability (HIGH-01): `GET /api/db/stats`, `GET /api/db/operations`,
    `GET /api/db/ollama/health`
  * execution + registry: `POST /api/execute`, `POST /api/tools/regression`,
    `GET /api/registry`, `POST /api/registry/reload`
  * job deletes: `DELETE /api/jobs`, `DELETE /api/jobs/{job_id}`
    (GETs are analyst per Q4)
  * Splunk boundary admit/purge: `POST /api/splunk-boundary/admit`,
    `DELETE /api/splunk-boundary/batches/{batch_id}` (status is analyst per Q5)
  * destructive case/notable/evidence paths (Q1 admin-only):
    `POST /api/db/triage/{case_id}/delete`, `POST /api/db/triage/batch-delete`,
    `DELETE /api/db/notables/{event_id}`, `POST /api/db/notables/{event_id}/delete`,
    `POST /api/db/notables/batch-delete`,
    `DELETE /api/db/triage/{case_id}/evidence/{evidence_id}`,
    `POST /api/db/triage/{case_id}/evidence/{evidence_id}/delete`,
    `POST /api/db/triage/{case_id}/evidence/batch-delete`,
    `POST /api/db/triage/{case_id}/evidence/delete-all`
* **analyst (50 routes)** — the investigation flow: cases, notables, evidence
  (create/edit/close), analyses, closure, rules/playbooks **writes** (Q2 analyst),
  reports, tool catalog **reads** (Q3 analyst: `GET /api/tools*`), `GET /api/jobs*`
  (Q4 analyst), `GET /api/splunk-boundary/status` (Q5 analyst), read-only Splunk search.
* **public (5 routes)** — `/health`, `/api/health`, `/api/auth/login`,
  `/api/auth/session`, `/api/auth/dev-login` (must answer before login;
  `/api/auth/*` is exempt in every mode, and dev-login does its own armed-key
  check inside the handler).

Note the asymmetry this creates: analyst-visible routes keep working exactly as
today for an **API key** caller (the key resolves as the admin machine actor),
so enforcement only ever restricts *session/proxy users*, never automation.

## Decisions (answered 2026-10-26 work order, supersedes Oct 14)

1. **Deletes: admin-only.** The Oct 14 "analyst deletes stay" is superseded:
   analysts may create/edit/close; permanent deletion requires admin.
2. **Playbook & playbook-card writes: analyst.**
3. **Tool catalog & execution reads: analyst** (`GET /api/tools*` relaxed to analyst; execution stays admin).
4. **`GET /api/jobs*`: analyst** (relaxed; deletes stay admin).
5. **`GET /api/splunk-boundary/status`: analyst**.

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
| 1 | `GET` | `/api/` | no | - | analyst | **analyst** |
| 2 | `POST` | `/api/auth/dev-login` | yes | - | - | **public** |
| 3 | `POST` | `/api/auth/login` | yes | - | - | **public** |
| 4 | `POST` | `/api/auth/logout` | yes | - | analyst | **analyst** |
| 5 | `GET` | `/api/auth/session` | no | - | - | **public** |
| 6 | `POST` | `/api/code-review` | yes | - | analyst | **analyst** |
| 7 | `POST` | `/api/code-review/fix` | yes | - | analyst | **analyst** |
| 8 | `POST` | `/api/code-review/sections` | yes | - | analyst | **analyst** |
| 9 | `POST` | `/api/code-review/zip` | yes | - | analyst | **analyst** |
| 10 | `GET` | `/api/code-reviews` | no | - | analyst | **analyst** |
| 11 | `GET` | `/api/code-reviews/{review_id}` | no | - | analyst | **analyst** |
| 12 | `POST` | `/api/db/analyze` | yes | - | analyst | **analyst** |
| 13 | `POST` | `/api/db/closure-note` | yes | - | analyst | **analyst** |
| 14 | `GET` | `/api/db/notables` | no | - | analyst | **analyst** |
| 15 | `POST` | `/api/db/notables/backfill-closure-notes` | yes | - | analyst | **analyst** |
| 16 | `POST` | `/api/db/notables/batch-delete` | yes | - | admin | **admin** |
| 17 | `GET` | `/api/db/notables/generate-fetch-spl` | no | - | analyst | **analyst** |
| 18 | `POST` | `/api/db/notables/generate-fetch-spl` | yes | - | analyst | **analyst** |
| 19 | `GET` | `/api/db/notables/historical` | no | - | analyst | **analyst** |
| 20 | `POST` | `/api/db/notables/paste` | yes | - | analyst | **analyst** |
| 21 | `DELETE` | `/api/db/notables/{event_id}` | yes | - | admin | **admin** |
| 22 | `GET` | `/api/db/notables/{event_id}` | no | - | analyst | **analyst** |
| 23 | `POST` | `/api/db/notables/{event_id}/delete` | yes | - | admin | **admin** |
| 24 | `POST` | `/api/db/notables/{event_id}/promote` | yes | - | analyst | **analyst** |
| 25 | `GET` | `/api/db/ollama/health` | no | - | admin | **admin** |
| 26 | `GET` | `/api/db/operations` | no | - | admin | **admin** |
| 27 | `GET` | `/api/db/placeholder-aliases` | no | - | analyst | **analyst** |
| 28 | `POST` | `/api/db/placeholder-aliases` | yes | - | analyst | **analyst** |
| 29 | `GET` | `/api/db/placeholder-aliases/suggestions` | no | - | analyst | **analyst** |
| 30 | `DELETE` | `/api/db/placeholder-aliases/{alias_id}` | yes | - | analyst | **analyst** |
| 31 | `PUT` | `/api/db/placeholder-aliases/{alias_id}` | yes | - | analyst | **analyst** |
| 32 | `GET` | `/api/db/rules` | no | - | analyst | **analyst** |
| 33 | `GET` | `/api/db/stats` | no | - | admin | **admin** |
| 34 | `GET` | `/api/db/supportive-queries` | no | - | analyst | **analyst** |
| 35 | `POST` | `/api/db/supportive-queries` | yes | - | analyst | **analyst** |
| 36 | `POST` | `/api/db/supportive-queries/draft` | yes | - | analyst | **analyst** |
| 37 | `POST` | `/api/db/supportive-queries/import-results` | yes | - | analyst | **analyst** |
| 38 | `GET` | `/api/db/supportive-queries/status/{case_id}` | no | - | analyst | **analyst** |
| 39 | `DELETE` | `/api/db/supportive-queries/{query_id}` | yes | - | analyst | **analyst** |
| 40 | `PUT` | `/api/db/supportive-queries/{query_id}` | yes | - | analyst | **analyst** |
| 41 | `GET` | `/api/db/triage` | no | - | analyst | **analyst** |
| 42 | `POST` | `/api/db/triage/batch-delete` | yes | - | admin | **admin** |
| 43 | `GET` | `/api/db/triage/{case_id}` | no | - | analyst | **analyst** |
| 44 | `PUT` | `/api/db/triage/{case_id}/analysis-draft` | yes | - | analyst | **analyst** |
| 45 | `GET` | `/api/db/triage/{case_id}/closure-readiness` | no | - | analyst | **analyst** |
| 46 | `POST` | `/api/db/triage/{case_id}/delete` | yes | - | admin | **admin** |
| 47 | `GET` | `/api/db/triage/{case_id}/evidence` | no | - | analyst | **analyst** |
| 48 | `POST` | `/api/db/triage/{case_id}/evidence` | yes | - | analyst | **analyst** |
| 49 | `POST` | `/api/db/triage/{case_id}/evidence/batch-delete` | yes | - | admin | **admin** |
| 50 | `POST` | `/api/db/triage/{case_id}/evidence/delete-all` | yes | - | admin | **admin** |
| 51 | `DELETE` | `/api/db/triage/{case_id}/evidence/{evidence_id}` | yes | - | admin | **admin** |
| 52 | `POST` | `/api/db/triage/{case_id}/evidence/{evidence_id}/delete` | yes | - | admin | **admin** |
| 53 | `GET` | `/api/db/triage/{case_id}/investigation-state` | no | - | analyst | **analyst** |
| 54 | `GET` | `/api/db/triage/{case_id}/notable` | no | - | analyst | **analyst** |
| 55 | `POST` | `/api/execute` | yes | yes | admin | **admin** |
| 56 | `GET` | `/api/health` | no | - | - | **public** |
| 57 | `DELETE` | `/api/jobs` | yes | yes | admin | **admin** |
| 58 | `GET` | `/api/jobs` | no | - | analyst | **analyst** |
| 59 | `DELETE` | `/api/jobs/{job_id}` | yes | yes | admin | **admin** |
| 60 | `GET` | `/api/jobs/{job_id}` | no | - | analyst | **analyst** |
| 61 | `GET` | `/api/registry` | no | - | admin | **admin** |
| 62 | `POST` | `/api/registry/reload` | yes | - | admin | **admin** |
| 63 | `GET` | `/api/reports` | no | - | analyst | **analyst** |
| 64 | `GET` | `/api/reports/{report_name}` | no | - | analyst | **analyst** |
| 65 | `POST` | `/api/splunk-boundary/admit` | yes | yes | admin | **admin** |
| 66 | `DELETE` | `/api/splunk-boundary/batches/{batch_id}` | yes | yes | admin | **admin** |
| 67 | `GET` | `/api/splunk-boundary/status` | no | - | analyst | **analyst** |
| 68 | `GET` | `/api/splunk/saved-searches` | no | yes | analyst | **analyst** |
| 69 | `POST` | `/api/splunk/search-one` | yes | yes | analyst | **analyst** |
| 70 | `GET` | `/api/splunk/search-status` | no | yes | analyst | **analyst** |
| 71 | `GET` | `/api/tool-artifacts/{artifact_path:path}` | no | - | analyst | **analyst** |
| 72 | `GET` | `/api/tools` | no | - | analyst | **analyst** |
| 73 | `POST` | `/api/tools/regression` | yes | yes | admin | **admin** |
| 74 | `GET` | `/api/tools/{tool_name}` | no | - | analyst | **analyst** |
| 75 | `GET` | `/health` | no | - | - | **public** |

**75 routes** — 20 proposed **admin**, 50 **analyst**, 5 **public**.

*Completeness check:* `app.openapi()` serves **75** method+path pairs, this matrix lists **75** — match.
<!-- END GENERATED ROUTE MATRIX -->
