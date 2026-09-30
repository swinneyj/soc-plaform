# Execution Playbook — SOC Platform roadmap (expands dev-plan §11)

**Audience:** executor agents (glm-5.3-flash class). Follow steps **in order, one stage per
session**. Each step is atomic: it names exact files, exact edits, and a `Verify:` line that
MUST pass before you continue. If a verify fails, **STOP** and report — do not improvise.

---

## 0. Standing rules (read once per session)

### Guardrails (never violate)
- Never touch `.env`, BWS, keychain, or any secret value. Never print a secret (the API key
  appears in served HTML — do not `curl` that HTML into your output).
- Never `git push` to `main`. Work only on `dev-dalton`.
- **Never create new test files.** The test-file count must stay **8** (`tests/test_*.py`).
  Add tests to `tests/test_investigation_fixes.py` or `tests/test_api_analyze_flow.py`.
- Never delete `commander.py`, `seed_test_cases.py`, `seed_supportive_results.py`,
  `supportive_rules.json`, `placeholder_aliases.json`, `data_source_catalog.json`, or
  anything under `api/`, `services/`, `web/` unless a step says so explicitly.
- The Windows ops set (`*.ps1`, `*.bat`, `Reset-DummyDb.ps1`, `git-flow*`,
  `ai-analysis-helper.ps1`, `make-updates-zip.ps1`) is **HOLD** — leave untouched (decision
  blocked on the Vercel handoff).
- If `services/mock_splunk/seed/seed_events.jsonl` shows a diff with ONLY timestamp changes,
  revert it (`git checkout -- <file>`) — the seeder churns timestamps on every run.

### Gates (run after every code-touching step, before committing)
```bash
.venv314/bin/python -m pytest 2>&1 | tail -1        # expect: N passed
.venv/bin/python -m pytest 2>&1 | tail -1           # expect: N passed (same N)
PYTHON=.venv314/bin/python bash scripts/check_undefined_names.sh   # expect: OK
bash scripts/check_no_merge_markers.sh              # expect: OK
bash scripts/baseline --verify 2>&1 | tail -2       # expect: BASELINE HOLDING
for f in $(find web -name '*.js' -not -path 'web/Old_archive/*'); do node --check "$f" || echo "FAIL $f"; done   # expect: no FAIL
```
UI-harness scenarios (run each separately; 23 total must stay green when you touch `web/`):
```bash
node tests/ui_regression/evidence_promote_load.mjs <scenario>   # scenarios:
# save_supportive save_phase2 save_busy_guard load_saved_evidence load_saved_error
# load_saved_no_case promote promote_historical_guard promote_failure promote_all_open
# closure_readiness_punchlist closure_blocked_generate_punchlist draft_autosave_debounce
# evidence_ledger_view loop_timeline_panel snapshot_migration_folds_legacy_maps
# api_layer_canonical_fallback stepper_guards
node tests/ui_regression/run_splunk_search_status.mjs <scenario>  # success failure no_case empty_spl running_to_complete_transitions
```

### Commit protocol (every stage)
```bash
printf '%s\n' '<type>: <summary>' '' '<bullet details>' '' \
  'Gates: pytest 276x2 (3.14+3.9), <other gates run>.' '' \
  'Generated with Codebuff 🤖' 'Co-Authored-By: Codebuff <noreply@codebuff.com>' > /tmp/msg
git add <files-this-stage-only>        # never `git add -A`
git commit -F /tmp/msg
git push origin dev-dalton
sleep 8; RUN_ID=$(gh run list --repo swinneyj/soc-plaform --workflow=Tests --limit 1 --json databaseId --jq '.[0].databaseId') && gh run watch "$RUN_ID" --repo swinneyj/soc-plaform --exit-status >/dev/null 2>&1; gh run list --repo swinneyj/soc-plaform --workflow=Tests --limit 1 --json conclusion --jq '.[0].conclusion'   # expect: success
```

### Gotchas learned the hard way
- `pytest.ini` already sets `addopts = -q`. Do NOT pass `-q` again (it hides the summary).
- `scripts/check_undefined_names.sh` needs `PYTHON=.venv314/bin/python` or it fails on missing pyflakes.
- When editing any `web/**/*.js`, bump its `?v=N` cache-buster in `web/index.modular.html`.
- macOS: BSD sed/awk — avoid `sed -i` ranges; prefer `str_replace`-style edits or python.
- VM-harness stubs must use canonical URLs (`/api/cases`, `/api/evidence/...`) — the api.js
  transport canonicalizes before dialing; stubs matching `/api/db/...` never fire.
- Harness file reads need `await fsRead(path, 'utf8')`; use the `ROOT` export from `harness_core.mjs`.
- The API doc guard (`TestCanonicalRestPaths`) requires every registered `/api*|/health` route
  to appear literally in `docs/API_ENDPOINTS.md` — update that doc whenever routes change.

---

## Stage A1 — Close the GET-mutation holes (P0 · security)

**Objective:** every destructive action requires the armed API key. Today three GET surfaces
mutate while the method-based gate (POST/PUT/PATCH/DELETE only) waves them through:
`GET /api/db/notables/{event_id}/delete`, `GET /api/db/triage/{case_id}/delete`, and
`GET /api/db/triage?delete_case_id=…&delete_analysis=…` (deletes "before listing").
The UI's `deleteTriageCase` currently uses the third one.

**A1.1 — Remove the GET wrappers.**
- `api/routes/notables.py` ~line 1875: delete the whole wrapper:
  ```python
  @router.get("/api/db/notables/{event_id}/delete", tags=["Database"])
  def delete_pasted_notable_get(event_id: int):
      """GET wrapper for delete_pasted_notable for environments that disallow POST/DELETE."""
      return delete_pasted_notable(event_id)
  ```
- `api/routes/triage.py` ~line 284: delete the whole wrapper:
  ```python
  @router.get("/api/db/triage/{case_id}/delete", tags=["Database"])
  def delete_triage_case_get(case_id: str, delete_analysis: bool = Query(False, description="Also delete analysis results for this case")):
      """GET wrapper for delete_triage_case for environments that disallow POST."""
      return delete_triage_case(case_id=case_id, delete_analysis=delete_analysis)
  ```
- Keep the POST routes (`/api/db/notables/{event_id}/delete`, `/api/db/triage/{case_id}/delete`)
  untouched.
- Verify: `grep -n "delete_.*_get" api/routes/*.py` → no matches.

**A1.2 — Strip delete side-effects from the GET list route.**
- `api/routes/triage.py` ~line 120: in the `GET /api/db/triage` signature remove the two params:
  ```python
      delete_case_id: Optional[str] = Query(None, description="If provided, delete this case before listing"),
      delete_analysis: bool = Query(False, description="Also delete analysis results for this case when deleting"),
  ```
- Then delete the entire `if delete_case_id:` block that follows (~line 132 through its closing
  lines, up to where the plain listing begins). Read lines 125–155 first to see the full block.
- Verify: `grep -n "delete_case_id\|delete_analysis" api/routes/triage.py` → only the POST
  handler's `delete_analysis` (line ~248) and the shared helper (line ~22) remain.

**A1.3 — Convert the frontend off the GET delete path.**
- `web/modules/api.js`, next to `deleteNotable` (~line 166), add (POST delete takes
  `delete_analysis` as a **Query** param — see `api/routes/triage.py:248`):
  ```js
  deleteCase(caseId, deleteAnalysis) {
      return post('/db/triage/' + encodeURIComponent(caseId) + '/delete'
          + (deleteAnalysis ? '?delete_analysis=true' : ''));
  },
  ```
- `web/modules/database.js` `deleteTriageCase` (~line 461): replace the `API.triage({delete_case_id…})`
  call with:
  ```js
                await API.deleteCase(case_.case_id, this.deleteAnalysisWithCase);
                this.triageData = await API.triage();
  ```
- Bump cache-busters in `web/index.modular.html`: `api.js?v=11` → `v=12`, `database.js?v=19` → `v=20`.
- Verify: `grep -rn "delete_case_id" web/ tests/` → no matches. Run the full harness list (§0).

**A1.4 — Path-aware mutation gate (belt and suspenders).**
- `api/auth.py`, replace the body of `mutation_gate_rejects` with:
  ```python
      path = request.url.path
      destructive_suffix = path.endswith(("/delete", "/batch-delete", "/delete-all"))
      return bool(
          _API_KEY
          and path.startswith("/api/")
          and (
              request.method in ("POST", "PUT", "PATCH", "DELETE")
              or (request.method == "GET" and destructive_suffix)
          )
      )
  ```
- Verify: gates green — no existing test GETs a `/delete` path (confirmed: zero usages).

**A1.5 — Update `docs/API_ENDPOINTS.md`.**
- Line ~69: change the `GET /api/cases` row description to `List triage cases.` (drop the
  `delete_case_id`/`delete_analysis` mention).
- Delete the two rows: `| GET | \`/api/cases/{case_id}/delete\` …` (line ~72) and
  `| GET | \`/api/notables/{event_id}/delete\` …` (line ~90).
- Verify: `PYTHON=.venv314/bin/python -m pytest tests/test_investigation_fixes.py::TestCanonicalRestPaths 2>&1 | tail -1` → passed.

**A1.6 — Route-semantics audit test (locks the bug class forever).**
- Append to `tests/test_investigation_fixes.py` (NOT a new file):
  ```python
  class TestRouteSemanticsAudit:
      """A1: every GET route must be read-only. New GET routes must be added here
      after a human classifies them — a failure means an unreviewed GET route (and
      thus a potential unauthenticated mutation) exists."""

      READ_ONLY_GET_ALLOWLIST = {
          "/api/", "/health", "/api/health", "/api/db/ollama/health",
          "/api/db/operations", "/api/db/stats",
          "/api/db/triage", "/api/db/triage/{case_id}",
          "/api/db/triage/{case_id}/closure-readiness",
          "/api/db/triage/{case_id}/evidence",
          "/api/db/triage/{case_id}/investigation-state",
          "/api/db/triage/{case_id}/notable",
          "/api/db/notables", "/api/db/notables/generate-fetch-spl",
          "/api/db/notables/historical", "/api/db/notables/{event_id}",
          "/api/db/placeholder-aliases", "/api/db/placeholder-aliases/suggestions",
          "/api/db/rules", "/api/db/supportive-queries",
          "/api/db/supportive-queries/status/{case_id}",
          "/api/code-reviews", "/api/code-reviews/{review_id}",
          "/api/jobs", "/api/jobs/{job_id}", "/api/registry",
          "/api/reports", "/api/reports/{report_name}",
          "/api/tool-artifacts/{artifact_path:path}",
          "/api/tools", "/api/tools/{tool_name}",
          "/api/splunk-boundary/status",
          "/index.modular.html",
      }

      def test_every_get_route_is_classified_read_only(self):
          get_paths = {
              route.path
              for route in api_main.app.routes
              if "GET" in getattr(route, "methods", set())
          }
          assert get_paths == self.READ_ONLY_GET_ALLOWLIST, (
              "GET routes changed — classify each new route read-only (add to "
              "allowlist) or make it a gated mutation. Removed routes must leave "
              "the allowlist. Diff: "
              f"unclassified={sorted(get_paths - self.READ_ONLY_GET_ALLOWLIST)} "
              f"stale={sorted(self.READ_ONLY_GET_ALLOWLIST - get_paths)}"
          )

      def test_no_get_route_is_delete_shaped(self):
          for route in api_main.app.routes:
              if "GET" in getattr(route, "methods", set()):
                  assert not route.path.endswith(("/delete", "/batch-delete", "/delete-all")), (
                      f"GET {route.path} is delete-shaped — destructive actions must be "
                      "POST/DELETE so the API-key mutation gate covers them"
                  )
  ```
  (`api_main` is already imported in that file. If the equality set mismatches on
  `/docs`-style routes, the test env has `ENABLE_DOCS` unset so docs routes do not exist —
  do not add them.)
- Verify: `PYTHON=.venv314/bin/python -m pytest tests/test_investigation_fixes.py::TestRouteSemanticsAudit 2>&1 | tail -1` → passed, and negative control: temporarily re-add
  a `@router.get(".../delete")` stub → test FAILS → remove stub again.

**A1.7 — Gates (full §0 list incl. 23 harness scenarios), commit `fix: destructive actions now require the armed API key (remove GET mutation surfaces)`, push, CI green.**

---

## Stage A2 — Supply chain (P0)

**A2.1 — Prove the unused deps.** Run:
```bash
grep -rln "import redis\|from redis\|import pptx\|from pptx\|import cryptography\|from cryptography" api/ services/ db/ web/ scripts/ Tools/ *.py 2>/dev/null
```
Expect: only `scripts/attic/…` hits (or none). If a LIVE file appears, STOP and report.

**A2.2 — Prune + pin `requirements.txt`.** Remove `redis`, `python-pptx`, `cryptography`.
Then pin what remains to the versions installed in `.venv314`:
```bash
.venv314/bin/python -m pip freeze | grep -iE '^(requests|sqlalchemy|psycopg|pydantic|fastapi|uvicorn|python-dotenv|python-multipart)='
```
Rewrite `requirements.txt` as one `name==X.Y.Z` per line (keep `psycopg[binary]==X.Y.Z`
spelling). Leave `requirements-dev.txt` names but pin them the same way.
- Verify: `.venv314/bin/python -m pytest 2>&1 | tail -1` → passed (imports still resolve).

**A2.3 — Audit.**
```bash
.venv314/bin/python -m pip install pip-audit >/dev/null 2>&1
.venv314/bin/python -m pip-audit -r requirements.txt
```
If vulnerabilities are reported: bump the affected pin to the fixed version, re-run §0 gates.
Record the final (clean or accepted-risk) result in the commit body.

**A2.4 — Dependabot.** Create `.github/dependabot.yml`:
```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule:
      interval: "weekly"
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule:
      interval: "weekly"
```
Enable alerts (needs repo admin):
```bash
gh api repos/swinneyj/soc-plaform/vulnerability-alerts -X PUT
```
Expect HTTP 204. If 403/404: note it in the commit body — the human flips it in
Settings → Code security (tracker checkbox).

**A2.5 —** Gates, commit `build: pin and prune dependencies, add pip-audit + Dependabot`, push, CI green.

---

## Stage A3 — Doc-status sync (P0)

**A3.1 — `docs/security-remediation-tracker.md`:**
- Line ~167: `- [ ] Wire \`SOC_CONFIG.apiKey\` into the served pages (Vercel deploy-time injection) — unlocks \`restricted\` mode + full-gate activation`
  → `- [x] **Done Sept 30 (local path):** server-injected \`window.SOC_CONFIG\` in \`/index.modular.html\` when the gate is armed (commit 99ee625). Vercel deploy-time variant tracked in the D1 handoff.`
- Lines ~48–49 (`Add an API-key/token dependency …` / `Restrict exposure …`): mark the first
  option `[x]` with note `(covered by the method-based mutation middleware + A1 path-aware gate)`; leave the exposure option `[ ]`.
- Line ~52: append `**Resolved Sept 30:** the injection design decision is made — server-side injection at serve time.`
**A3.2 — `docs/BACKEND_MODULARIZATION_PLAN.md` line 4:**
`**Status:** Phase 1 landed 2026-09-29 (further phases planned)` →
`**Status:** complete (Sept 29–30, 2026) — Pieces A–D all landed: 11 routers in api/routes/, api/schemas.py + api/helpers/ in place, api/main.py is a ~220-line app shell, REPO_MAP.md points at the new layout.`
**A3.3 —** Gates (docs-only: pytest ×2 + `check_no_merge_markers` suffice), commit `docs: sync tracker + backend-plan statuses to reality`, push, CI green.

---

## Stage B1 — Root triage (P1)

**B1.1 — Attic the zero-ref one-shots** (all confirmed unreferenced except where noted):
```bash
git mv add_code_review.py inspect_db.py ingest_notables.py wipe_db.py \
       seed_dummy_closed_notables.py close_freebuff_tabs.py scripts/attic/
```
**B1.2 — Keep (do not touch):** `commander.py` (live via `Tools/automated_reporter`),
`seed_test_cases.py`, `seed_supportive_results.py`.
**B1.3 — HOLD (skip):** the whole Windows ops set (see §0 guardrails).
**B1.4 — Fix the one dangling ref:** `docs/security-remediation-tracker.md` mentions
`add_code_review.py` — update that mention to `scripts/attic/add_code_review.py`.
**B1.5 —** Gates, commit `chore: attic dead root one-shots`, push, CI green.

---

## Stage B2 — Debris sweep (P1)

Facts verified: `sample_rules.json` and `updated_rules.json` are **byte-identical**
(sha1 `dc83bcc64cda6d2c9005e0242cc60d26112372a4`).

**B2.1 — Attic binaries and dead files:**
```bash
git mv Project_Structure.docx Test-Branch-Workthrough.docx \
       SOC_Automation_Architecture.pptx \
       SOC_Platform_Feature_Presentation_FINAL.pptx \
       SOC_Platform_Feature_Presentation_FINAL_21SLIDES_V2.pptx \
       SOC_Platform_Feature_Presentation_FINAL_21SLIDES_V2_UPDATED.pptx \
       updated_rules.json payload.json unsupported_rule_splunk_results.csv \
       closure_form_snippet.html code_review_ui.html BUILD_SUMMARY.md \
       Downloads_Map.txt README_MOVED_TO_SOC_Automation_Working.txt \
       README.txt SOC_Automation_Working.code-workspace-2.code-workspace \
       SOC_Automation_Working_clean.code-workspace scripts/attic/
```
Note: `Downloads_Map.txt` is a **generated artifact** (written by `Tools/dir_mapper`) — the
attic copy is historical; the tool regenerates it on use.
**B2.2 — Move the living doc:** `git mv multi-user-workflow.md docs/multi-user-workflow.md`
and update `README.md` line ~100: `(multi-user-workflow.md)` → `(docs/multi-user-workflow.md)`.
**B2.3 — Fix REPO_MAP refs:** `docs/REPO_MAP.md` lines ~17 and ~85 list `README.txt` —
change to `README.md` (the survivor) and, where the line described README.txt's role, say
`README.md (README.txt archived to scripts/attic/)`.
**B2.4 — Keep:** `sample_rules.json`, `supportive_rules.json`, `placeholder_aliases.json`,
`data_source_catalog.json`, `README.md`, `PROJECT_SPEC.md`, `OPERATOR_CHEAT_SHEET.md`,
`RUNBOOK.md`, `AGENTS.md`, `CONTAINERIZATION.md`, `SETUP.md`, `BUILD_SUMMARY`… (already moved).
**B2.5 —** Gates, commit `chore: sweep root debris, single README, docs relocated`, push, CI green.

---

## Stage B3 — Tools staging copy (P1)

**B3.1 — Verify dead:** `grep -rln "staging_copy\|chat_language_models_2_" Tools/ api/ services/ scripts/ tests/ docs/` → only the directory itself + the dev-plan mention.
**B3.2 —** `git mv "Tools/chat_language_models_2_-_staging_copy" scripts/attic/tools-chat_language_models_2-staging_copy`
**B3.3 —** Gates, commit `chore: attic Tools staging copy`, push, CI green.

---

## Stage B4 — Deployment-story matrix (P1)

**B4.1 —** Append to `docs/SETUP.md` (before any final appendix):
```markdown
## Deployment stories (support matrix)

| Story | Status | Entry point | Notes |
|-------|--------|-------------|-------|
| Mac workstation (canonical) | **Supported** | `scripts/start` | Start-riding ops: backup (20h staleness skip) + retention dry-run on every start; `scripts/start --backup` on demand. BWS/keychain secrets. |
| Docker Compose (Linux/Windows) | Secondary | `scripts/start_platform.sh` | Postgres+Redis+api-service via compose; shared-dump restore. |
| Vercel (hosted API + preview) | In progress | `.github/workflows/deploy.yml` | Env handoff pending (see `docs/VERCEL_HANDOFF.md`); routing config lives in the Vercel dashboard (no `vercel.json` in-repo) — determines whether the SOC_CONFIG injection applies. |
| launchd/cron schedules | **Deliberately not used** | — | Ops rides `scripts/start` by decision (Sept 30). `scripts/local.soc-platform.backup.plist` exists but is not installed. |
```
**B4.2 —** Gates (docs-only), commit `docs: deployment-story support matrix`, push, CI green.

---

## Stage B5 — .gitignore BOM (P1)

**B5.1 —** Strip the UTF-8 BOM (`.gitignore` currently starts with `﻿`):
```bash
.venv314/bin/python - <<'PY'
data = open('.gitignore', 'rb').read()
assert data.startswith(b'\xef\xbb\xbf'), 'no BOM found — already clean, skip'
open('.gitignore', 'wb').write(data[3:])
print('BOM stripped')
PY
```
**B5.2 —** Verify `git diff .gitignore` shows only the first-line change; gates (docs-only);
commit `chore: strip .gitignore BOM`, push, CI green.

---

## Stage C1 — Session auth + roles: write the plan first (P2)

**C1.1 — Create `docs/SESSION_AUTH_PLAN.md`** with exactly these sections (fill each with a
concrete design; keep the modularization-plan tone: steps, risk controls, size target):
1. **Problem & non-goals** — API-key alone can't distinguish analysts from admins and can't
   be revoked per-user; non-localhost exposure is blocked on this (security tracker).
2. **Design** — session cookie (HttpOnly, SameSite=Lax, Secure) + CSRF token for the UI;
   `X-API-Key` path stays for API clients; `api/auth.py` remains the single seam.
3. **Roles** — `analyst` (investigation flow) vs `admin` (tools/registry, jobs delete,
   boundary admit/release, retention apply, backups). Map each existing route family to a
   role in a table (use the `docs/API_ENDPOINTS.md` families).
4. **Storage** — `users` + `sessions` tables in `db/models.py`; password hashing via
   `hashlib.scrypt` (stdlib only — no new deps).
5. **Step plan** — Piece A models+migrations-free create_all, Piece B login/logout routes
   + middleware, Piece C UI login gate + role-aware buttons, Piece D tests (auth matrix).
6. **Risk controls & rollback** — gate stays until sessions exist; feature-flag via env.
7. **Size target & exit criteria** — tests for: login, logout, CSRF reject, role gate,
   API-key still works.
**C1.2 —** Gates (docs-only), commit `docs: session auth + roles plan (C1)`, push, CI green.

---

## Stage C2 — Service hardening (P2 · do C2.0 scoping as its own commit first)

**C2.0 — Scoping doc:** append a `## Hardening limits` section to `docs/DEVELOPMENT_PLAN.md`
§11-C2 area listing proposed limits (each with default + where enforced):
analyze wall-clock timeout (e.g. 300s, `api/routes/analyze.py`), job queue cap
(`api/routes/tools.py` `_JOBS`), artifact size cap (`services/artifact_guard.py`),
paste payload limit (`api/routes/notables.py` paste route), and a simple in-memory rate
limit for Ollama-backed POSTs (middleware in `api/main.py`). Commit as `docs: hardening limits scoping`.
**C2.1 — Implement each limit** one commit each: constant at top of the named file,
enforcement at the named site, one test in `tests/test_investigation_fixes.py` per bound
(exceed → 413/429/504 as appropriate). Follow §0 gates per commit.

---

## Stage C3 — Multi-model per-stage choice (P2)

**C3.1 — API:** `api/schemas.py` `AnalyzeRequest`: add optional
`stage_models: dict[str, str]` (keys `initial`, `phase2`, `final`; falls back to `model`).
`api/routes/analyze.py`: resolve the model per phase via
`stage_models.get(<phase>, request.model)`.
**C3.2 — UI:** `web/components/AnalysisTab.js` — Stage 3/4/5 headers each get a model
`<select>` bound to `stageModels.{initial,phase2,final}` (options from
`ollamaHealth.models`, default = current `analysisModel`); `web/modules/analysis.js` sends
`stage_models` in the analyze payload; `web/app.modular.js` holds the `stageModels` map.
Bump cache-busters for every changed web file.
**C3.3 — Tests:** `tests/test_api_analyze_flow.py` — one test asserting per-stage override
reaches the Ollama stub's `model` field and one asserting fallback to `model`.
Harness: extend `evidence_promote_load.mjs` `loop_timeline_panel`-adjacent scenario asserts
`stage_models` presence in the analyze POST body.
**C3.4 —** §0 gates (incl. 23 scenarios), commit `feat: per-stage model selection in the analysis wizard`, push, CI green.

---

## Stage C4 — Paste-box storage via boundary batch (P2)

**C4.1 —** Read `ingest_json_notables` docstring (`services/evidence_service.py`) — the
sanitization contract MUST stay. Route pasted-notable storage through a
`splunk_boundary` batch so manifest/purge parity holds (see `services/splunk_boundary.py`).
**C4.2 — Tests:** one test per property: paste → boundary manifest row exists;
purge batch → pasted rows removed; sanitization output byte-identical to today's.
**C4.3 —** §0 gates, commit `feat: paste-box storage through boundary batches (manifest/purge parity)`, push, CI green.

---

## Stage D1 — Vercel handoff doc (P3 · assemble now, unblocks by conversation)

**D1.1 — Create `docs/VERCEL_HANDOFF.md`** with these sections and known content:
1. **Env vars to set in Vercel:** `DATABASE_URL` (Neon, rotated value — see
   `docs/security-remediation-tracker.md` §1), `API_KEY` (the armed key; same one BWS serves
   locally), `CORS_ORIGINS` (preview origins), leave `ENABLE_DOCS` unset.
2. **Open question (capture the answer during handoff):** no `vercel.json` exists — does the
   dashboard routing send `/index.modular.html` through the FastAPI function (injection
   works) or serve it from Vercel's CDN (injection skipped → UI writes 401)? If CDN, options:
   (a) route the document through the function, (b) build-time injection in deploy.yml.
3. **Deploy pipeline:** `deploy.yml` runs the baseline guard → `vercel build` → deploy →
   `/api/health` smoke; expected smoke output `{"status":"healthy", …}`.
4. **What breaks without each var** — table (no `DATABASE_URL` → degraded health-only; no
   `API_KEY` → open mutations; bad `CORS_ORIGINS` → preview UI blocked by CORS).
5. **Handoff checklist** for Justin (5 checkboxes).
**D1.2 —** Gates (docs-only), commit `docs: Vercel handoff package (D1)`, push, CI green.

---

## Stage D2 — Live Splunk rehearsal checklist (P3 · assemble now)

**D2.1 — Create `docs/SPLUNK_REHEARSAL.md`:** numbered rehearsal: (1) get `SPLUNK_URL` +
`SPLUNK_TOKEN` (read-only role); (2) `export SEARCH_BACKEND=splunk SPLUNK_URL=… SPLUNK_TOKEN=…`
and restart via `scripts/start`; (3) run one Stage-2 query via the UI "Run in Splunk";
(4) verify: CSV `_time` epoch → ISO normalization, stats-row shape, bearer header, boundary
latch interplay (`services/splunk_boundary.py` — QUARANTINED latch must not block read
searches); (5) failure drill: bad token → `SplunkSearchError` surfaces as `query_failed`
card status. Each step gets an expected-result line.
**D2.2 —** Gates (docs-only), commit `docs: live Splunk rehearsal checklist (D2)`, push, CI green.

---

## Stage D3 — User-side checklist (not automatable — record only)

Add a short `## User-side hygiene (owner: human)` list to `docs/VERCEL_HANDOFF.md` §5 or the
tracker: Safari history scrub, thread-history deletion decision. No code. No commit needed
beyond the doc that carries it.

---

## Definition of done

| Stage | DoD |
|-------|-----|
| A1 | No GET route mutates; audit test green + negative control proven; UI delete works via POST; 23 harness scenarios green |
| A2 | requirements pinned & pruned; pip-audit result recorded; Dependabot config + alerts attempted |
| A3 | Tracker + backend-plan statuses match reality |
| B* | Root = live platform + pointer docs; every move has its refs updated |
| C1 | Plan doc reviewed-by-human before C2/C3/C4 build |
| C2–C4 | Each limit/feature has ≥1 regression test in the existing test files |
| D1–D2 | Docs assembled so unblocking = one conversation/checklist run |
