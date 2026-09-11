# Backend Modularization Plan — api/main.py

**Date:** 2026-09-11  
**Status:** Planned (frontend modularization complete, this is the backend counterpart)  
**Goal:** Same treatment as `docs/FRONTEND_MODULARIZATION.md` — take a 5314-line monolith and split it into domain-owned files without downtime. Additive first, then cutover.

## Current state

| | File | Lines | Owns |
|---|------|-------|------|
| Monolith | `api/main.py` | 5314 | 47 `@app.*` routes + 30 helpers + 8 Pydantic models + job dict + CORS + lifespan + static mount |
| Already split | `api/routes/system.py` | 81 | `/health`, `/api/health`, `/api/`, `/api/db/ollama/health`, `/api/db/stats` |
| untouched | `services/*.py` | 200-700 | ollama, investigation_state, evidence, analysis helpers (already clean) |
| untouched | `db/models.py` | 203 | SQLAlchemy models |

Everything else is inline in `api/main.py` — tools, triage, notables, analyze (1200 lines), aliases, supportive queries, rules, closure notes, code review (300 lines). Every branch touches the same file → conflicts like `web/index.html` had.

## Target layout (mirrors web/ modularization)

```
api/
├── main.py                 # ~250-300 lines: FastAPI(), lifespan, CORS, include_router(s), static mount, exception_handler
├── schemas.py              # Pydantic models (JobStatus, ToolRequest, AnalyzeRequest, PastedNotableRequest, etc.)
├── helpers/                # Pure/defensive helpers moved out of main (no DB session, no app)
│   ├── notable_parser.py  # parse_pasted_notable, split_pasted_notables, normalize_notable_fields, etc.
│   ├── phase2.py           # _extract_phase2_queries, _ground_phase2_queries, _build_supportive_phase2_fallback, _annotate_phase2_targets
│   ├── catalog.py          # _load_data_source_catalog, _format_catalog_for_prompt
│   ├── evidence.py         # _load_supportive_results_for_case, _rebuild_investigation_state_from_evidence, _purge_case_related_records
│   └── code_sections.py    # _extract_python_sections, _extract_code_sections
└── routes/
    ├── system.py           # already exists (81 lines)
    ├── tools.py            # /api/tools, /api/tools/{name}, /api/execute, /api/jobs, /api/jobs/{id}, /api/reports, /api/registry
    ├── triage.py           # /api/db/triage*, /api/db/triage/{id}/investigation-state, closure-readiness
    ├── notables.py         # /api/db/notables/*, /api/db/notables/generate-fetch-spl, paste/promote/batch-delete
    ├── analysis.py         # /api/db/analyze + /api/db/triage/{id}/evidence*, /api/db/triage/{id}/notable, /api/db/triage/{id}/delete
    ├── rules.py            # /api/db/rules, /api/db/supportive-queries*, /api/db/placeholder-aliases*, /api/db/closure-note
    └── code_review.py      # /api/code-review*, /api/code-reviews*
```

Helpers can optionally stay in `api/helpers/*.py` or `services/` — choose `api/helpers/` if they are HTTP-adjacent, `services/` if reused outside the API.

## Mapping current routes → target file

| Current `@app.*` in `api/main.py` | Count | Target |
|-----------------------------------|-------|--------|
| `/api/tools`, `/api/execute`, `/api/jobs*`, `/api/reports*`, `/api/registry*` | 8 | `routes/tools.py` |
| `/api/db/triage`, triage helpers, `/delete`, `/batch-delete` | 9 | `routes/triage.py` |
| `/api/db/notables*`, `/paste`, `/promote`, `/historical` | 10 | `routes/notables.py` |
| `/api/db/analyze`, `/evidence*`, `/notable`, `/investigation-state` | 10 | `routes/analysis.py` |
| `/api/db/rules`, `/supportive-queries*`, `/placeholder-aliases*`, `/closure-note`, `/closure-readiness` | 11 | `routes/rules.py` |
| `/api/code-review*`, `/api/code-reviews*` | 6 | `routes/code_review.py` |
| `/health` etc. | 5 | `routes/system.py` (done) |

Total ~59 route handlers → 7 files, ~300-600 lines each.

## How to split safely (same additive-then-cutover as frontend)

1. **Piece A — Scaffolding (no behavior change)**
   - Create `api/schemas.py` — move Pydantic models, import in `api/main.py` (re-export for compatibility).
   - Create `api/helpers/` — move one pure helper group at a time (notable_parser first, verify `api/main.py` still imports it).
   - Create empty `api/routes/*.py` with `APIRouter()` and one trivial endpoint each, register in `api/main.py` via `include_router` — prove routing works alongside legacy routes.

2. **Piece B — Move domain by domain (logic move only)**
   - Order: `tools` (lowest risk, fewest deps) → `notables` → `triage` → `rules` (aliases/supportive) → `analysis` (largest, do last with reviews) → `code_review`.
   - Each move: copy handlers + their private helpers into the target router, switch `api/main.py` to delegate / remove the old handler, run smoke test. Keep one PR per domain so conflicts stay isolated.

3. **Piece C — Cutover**
   - Strip `api/main.py` to ~300 lines: imports, `app = FastAPI(...)`, `app.include_router(system)`, `...tools`, `...triage`, etc., `app.mount("/", StaticFiles(...))`, exception handler.
   - Backup `api/main.py.bak` already exists — add `api/main.py.pre-backend-split.bak` before cutover.

4. **Piece D — Cleanup**
   - Ensure `services/` vs `api/helpers/` ownership is documented (pure helpers that touch no DB → `api/helpers`, shared state helpers → `services`).
   - Update `docs/REPO_MAP.md` + `docs/API_DB_VISUAL_MAP.md` to point at new route files (already done for frontend; mirror that table).

## Risk controls

- Additive first: new files coexist with monolith until endpoint is verified, then old handler removed. Live never breaks.
- One domain per PR — same rule as `docs/FRONTEND_MODULARIZATION.md` design rule #1.
- Hard-refresh not needed for backend, but `docker compose up -d --build` + `curl /docs` (OpenAPI) to verify registry after each move.
- Keep `api/main.py.bak` and tagged git commit before Phase 2 so rollback is `cp api/main.py.pre-backend-split.bak api/main.py`.

## Size target after split

| File | Lines |
|------|-------|
| `api/main.py` | ~300 |
| `api/schemas.py` | ~250 |
| `api/helpers/*` | 150-400 each |
| Each `api/routes/*.py` | 300-600 |
| `api/routes/analysis.py` | ~800-1000 (still largest; okay — evidence + analyze live together) |

## Fast checks to add with this plan (no runtime needed)

- `scripts/check_no_merge_markers.ps1` / `.sh` — `grep -R "<<<<<<<"` fails CI if any marker remains.
- `docker compose config` — validates compose after API reshuffle.
- `python -m py_compile api/main.py api/routes/*.py api/helpers/*.py` — syntax gate (runs once CLT/Python lands).

## Related

- Frontend precedent: `docs/FRONTEND_MODULARIZATION.md`, `web/README-MODULAR.md`, `web/modules/README.md` — copy that doc structure and load-order table.
- Rollback reference: `web/Old/index.bak` pattern → `api/main.py.bak` already present.
