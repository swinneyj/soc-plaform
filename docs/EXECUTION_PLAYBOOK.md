# Execution Playbook — SOC Platform roadmap (expands dev-plan §11)

**Audience:** executor agents (glm-5.3-flash class). Follow steps **in order, one stage per
session**. Each step is atomic: it names exact files, exact edits, and a `Verify:` line that
MUST pass before you continue. If a verify fails, **STOP** and report — do not improvise.

**Fidelity tiers** (tagged on every stage header): `[mechanical]` = run cold, every edit is
specified. `[mechanical after sign-off]` = run only after the named human gate.
`[informational]` = record only, nothing to execute.

---

## 0. Standing rules (read once per session)

### Guardrails (never violate)
- Never touch `.env`, BWS, keychain, or any secret value. Never print a secret (the API key
  appears in served HTML — do not `curl` that HTML into your output).
- Never `git push` to `main`. Work only on `dev-dalton`.
- **Never create new test files.** The test-file count must stay **8** (`tests/test_*.py`).
  Add tests to `tests/test_investigation_fixes.py`, `tests/test_api_analyze_flow.py`, or
  `tests/test_api_key_gate.py` (auth tests go in the last one).
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

## Stage A1 — Close the GET-mutation holes (P0 · security) `[mechanical]`

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

## Stage A2 — Supply chain (P0) `[mechanical]`

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

## Stage A3 — Doc-status sync (P0) `[mechanical]`

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

## Stage B1 — Root triage (P1) `[mechanical]`

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

## Stage B2 — Debris sweep (P1) `[mechanical]`

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

## Stage B3 — Tools staging copy (P1) `[mechanical]`

**B3.1 — Verify dead:** `grep -rln "staging_copy\|chat_language_models_2_" Tools/ api/ services/ scripts/ tests/ docs/` → only the directory itself + the dev-plan mention.
**B3.2 —** `git mv "Tools/chat_language_models_2_-_staging_copy" scripts/attic/tools-chat_language_models_2-staging_copy`
**B3.3 —** Gates, commit `chore: attic Tools staging copy`, push, CI green.

---

## Stage B4 — Deployment-story matrix (P1) `[mechanical]`

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

## Stage B5 — .gitignore BOM (P1) `[mechanical]`

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

## Stage C1 — Session auth + roles: PLAN ✅ DONE (`docs/SESSION_AUTH_PLAN.md`)

The plan (Pieces A–D, roles matrix, storage, exit criteria) is written and committed
(`7281fe8`). **Human sign-off on that plan is the gate before C1B. Do not start C1B
without it.**

### Stage C1B — Session auth build (P2 · 4 commits) `[mechanical after sign-off]`

Design reference: `docs/SESSION_AUTH_PLAN.md`. Flag: `AUTH_MODE` — everything lands inert
until `AUTH_MODE=session`. Tests monkeypatch `api.auth._SESSION_MODE` (mirror the existing
`_API_KEY` monkeypatch pattern in `tests/test_api_key_gate.py`).

**C1B.1 — Piece A: models + scrypt + user CLI.** (commit 1)
- `db/models.py` — append (match the file's existing Column/import style):

  ```python
  class User(Base):
      __tablename__ = "users"
      id = Column(Integer, primary_key=True)
      username = Column(String(64), unique=True, nullable=False, index=True)
      password_hash = Column(String(256), nullable=False)
      role = Column(String(16), nullable=False, default="analyst")  # analyst|admin
      is_active = Column(Boolean, nullable=False, default=True)
      created_at = Column(DateTime, nullable=False, default=utcnow_naive)

  class AuthSession(Base):
      __tablename__ = "auth_sessions"
      id = Column(Integer, primary_key=True)
      token_hash = Column(String(64), unique=True, nullable=False, index=True)  # sha256 hex
      csrf_token = Column(String(64), nullable=False)
      user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
      created_at = Column(DateTime, nullable=False, default=utcnow_naive)
      expires_at = Column(DateTime, nullable=False)
  ```

- `api/auth.py` — append the scrypt helpers exactly as specced in SESSION_AUTH_PLAN §4
  (`hash_password` / `verify_password`, format `scrypt$n$r$p$salthex$hashhex`, n=2**14,
  r=8, p=1, dklen=32, `hmac.compare_digest`) plus `import hashlib, hmac, secrets` at top.
- `scripts/manage_users.py` — new CLI (stdlib + `api.auth.hash_password`): subcommands
  `create <name> --role analyst|admin`, `list`, `set-role <name> <role>`,
  `set-password <name>`, `deactivate <name>`. Password via `getpass` prompt or
  `--password-env VARNAME` (env var NAME, never the secret in argv).
- Tests in `tests/test_api_key_gate.py`: `test_scrypt_roundtrip`,
  `test_scrypt_wrong_password_fails`, `test_password_never_stored_plaintext`.
- Verify (isolated DB, never the real DATABASE_URL):
  `DATABASE_URL=sqlite:////tmp/auth_smoke.db .venv314/bin/python scripts/manage_users.py create smoke --role admin --password-env SMOKE_PW`
  with `SMOKE_PW=dummy123` exported → prints created; then `rm /tmp/auth_smoke.db`.
- Commit `feat: users + sessions models, scrypt helpers, manage_users CLI (C1B.1)`.

**C1B.2 — Piece B: auth routes + gates.** (commit 2)
- `api/routes/auth.py` — new router:

  ```python
  """Session auth routes (SESSION_AUTH_PLAN.md Piece B)."""
  import secrets

  from fastapi import APIRouter, HTTPException, Request, Response
  from pydantic import BaseModel

  from api import auth

  router = APIRouter()

  class LoginRequest(BaseModel):
      username: str
      password: str

  def _issue_session(response: Response, user) -> dict:
      from db.models import AuthSession, SessionLocal
      token = secrets.token_urlsafe(32)
      csrf = secrets.token_urlsafe(32)
      db = SessionLocal()
      try:
          row = AuthSession(
              token_hash=auth.hash_token(token),
              csrf_token=csrf,
              user_id=user.id,
              expires_at=auth.session_expiry(),
          )
          db.add(row); db.commit()
      finally:
          db.close()
      response.set_cookie(
          "soc_session", token,
          httponly=True, samesite="lax", secure=auth.secure_cookies(),
          max_age=12 * 3600,
      )
      return {"user": user.username, "role": user.role, "csrf_token": csrf}

  @router.post("/api/auth/login")
  def login(payload: LoginRequest, response: Response):
      from db.models import SessionLocal, User
      db = SessionLocal()
      try:
          user = db.query(User).filter(User.username == payload.username).first()
          if not user or not user.is_active or not auth.verify_password(payload.password, user.password_hash):
              raise HTTPException(status_code=401, detail="Invalid credentials")
          return _issue_session(response, user)
      finally:
          db.close()

  @router.post("/api/auth/logout")
  def logout(request: Request, response: Response):
      auth.destroy_session(request)
      response.delete_cookie("soc_session")
      return {"ok": True}

  @router.get("/api/auth/session")
  def session_info(request: Request):
      actor = auth.resolve_actor(request)
      if actor.kind != "session":
          raise HTTPException(status_code=401, detail="Not signed in")
      return {"user": actor.user.username, "role": actor.role, "csrf_token": actor.csrf_token}
  ```

- `api/auth.py` — append `hash_token` (sha256 hex), `session_expiry()` (now + 12h,
  naive UTC via `db.util.utcnow_naive`), `secure_cookies()` (env `SESSION_SECURE`),
  `session_mode()` (reads module `_SESSION_MODE` set from `AUTH_MODE` at import),
  `destroy_session(request)`, `resolve_actor(request) -> Actor` (Actor =
  `namedtuple("Actor", "kind role user csrf_ok csrf_token")`; kind: `session` when the
  cookie hashes to a live `AuthSession` row, `api-key` when `request_has_api_key`, else
  `anonymous`; session `csrf_ok` compares `X-CSRF-Token` header to `csrf_token` with
  `hmac.compare_digest`). Also `EXEMPT_PATHS = ("/health", "/api/health", "/api/auth/login", "/api/auth/session")`
  and `def require_role(role)` returning a FastAPI dependency raising 401 (anonymous) /
  403 (role below required).
- `api/main.py` — register `from api.routes.auth import router as auth_router;
  app.include_router(auth_router)` **before** the static mount block, and replace the
  `_api_key_mutation_gate` middleware body with:

  ```python
      path = request.url.path
      if auth.session_mode():
          actor = auth.resolve_actor(request)
          is_api = path.startswith("/api/")
          if is_api and path not in auth.EXEMPT_PATHS and actor.kind == "anonymous":
              return JSONResponse({"detail": "Authentication required"}, status_code=401)
          if auth.mutation_gate_rejects(request):
              if actor.kind == "anonymous":
                  return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
              if actor.kind == "session" and not actor.csrf_ok:
                  return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
      elif auth.mutation_gate_rejects(request) and not auth.request_has_api_key(request):
          return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
      return await call_next(request)
  ```

  (`mutation_gate_rejects` keeps its A1 path-aware form but must gate mutations in session
  mode even when `_API_KEY` is empty — restructure it to
  `bool(path.startswith("/api/") and (method mutating or destructive_suffix))` and let the
  middleware branch decide key-vs-session semantics.)
- Admin dependency on the §3 admin routes (10 decorators, add
  `dependencies=[Depends(require_role("admin"))]`): `GET /api/registry`,
  `POST /api/registry/reload`, `GET /api/tools`, `GET /api/tools/{tool_name}`,
  `POST /api/execute`, `POST /api/tools/regression`, `DELETE /api/jobs`,
  `DELETE /api/jobs/{job_id}`, `POST /api/splunk-boundary/admit`,
  `DELETE /api/splunk-boundary/batches/{batch_id}`.
- **Doc-drift guard:** add literal `docs/API_ENDPOINTS.md` rows for
  `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/session` or
  `TestCanonicalRestPaths` fails.
- Tests in `tests/test_api_key_gate.py` (a `with_session` helper that inserts a User +
  AuthSession via `api_client.test_session()`-style session and sets the cookie on the
  client): `test_login_ok_sets_cookie`, `test_login_bad_password_401`,
  `test_logout_invalidates`, `test_csrf_missing_403`, `test_session_read_gate_401`.
- Verify: those 5 pass with `_SESSION_MODE` monkeypatched on; the full suite passes with
  it off (flag-off byte-compat).
- Commit `feat: session login/logout + CSRF + role dependency (C1B.2)`.

**C1B.3 — Piece C: UI login gate + role-aware controls.** (commit 3)
- `web/utils/auth.js` — after the existing X-API-Key block, add CSRF stamping (same IIFE):

  ```js
    if (global.axios) {
        global.axios.interceptors.request.use(function (config) {
            config.headers = config.headers || {};
            if (global.__SOC_CSRF__) config.headers['X-CSRF-Token'] = global.__SOC_CSRF__;
            return config;
        });
    }
  ```

- Root state (`web/app.modular.js` near `analysisModel: ''`): add
  `auth: { user: '', role: '', csrf: '' },`; on startup fetch `GET /api/auth/session` —
  200 → store user/role and `window.__SOC_CSRF__ = csrf_token`; 401 → show login modal.
- `web/components/HeaderNav.js` — user chip + Logout button + login modal
  (username/password → `POST /api/auth/login` via `API.sessionLogin(payload)`; add
  `sessionLogin`/`sessionLogout`/`sessionInfo` methods to `web/modules/api.js`).
- Role-aware controls (hide + tooltip for non-admin, same pattern as the disabled
  historical-promote tooltip): ToolsTab execute/regression/registry-reload buttons,
  JobsTab delete/clear buttons, SplunkBoundaryWidget admit/release buttons. Pass `role`
  down as a prop — never `axios` in components (design rule #2).
- 401-on-mutation → reopen login modal (handle it in the `web/modules/api.js` transport
  error path so one place covers every call).
- Bump cache-busters for every touched file (`auth.js?v=1`, `HeaderNav.js?v=9`,
  `api.js` (v11→v12), `app.modular.js` (v19→v20), plus any tab component touched).
- Verify: `node --check` clean; all 23 harness scenarios green (they run with auth inert);
  live smoke with `AUTH_MODE=session` and a created user: login → Run All works →
  analyst account sees no admin buttons.
- Commit `feat: UI login gate + role-aware admin controls (C1B.3)`.

**C1B.4 — Piece D: auth matrix + doc status.** (commit 4)
- Remaining tests in `tests/test_api_key_gate.py`: `test_role_gate_analyst_403` (analyst
  session DELETEs `/api/jobs/x` → 403), `test_api_key_still_admin_in_session_mode`
  (X-API-Key passes without CSRF), `test_expired_session_401` (backdate `expires_at`).
- Update `docs/SESSION_AUTH_PLAN.md` Status line → built; run the suite with the flag ON
  and OFF.
- Commit `test: session auth matrix + flag-off compat (C1B.4)`.

---

## Stage C2 — Service hardening (P2) `[mechanical after sign-off]`

**Human gate:** confirm or edit the five defaults in C2.0 before C2.1.

**C2.0 — Commit the limits doc.** Append to `docs/DEVELOPMENT_PLAN.md` §11 (verbatim,
numbers are the PROPOSED DEFAULTS the human signs off):

```markdown
### Hardening limits (proposed defaults — signed off <DATE>)
| Limit | Default | Enforced at | Over-limit |
|---|---|---|---|
| Analyze wall-clock | 300 s | `api/routes/analyze.py` ollama call (worker thread + join(timeout)) | 504, nothing persisted |
| Tool job queue | 100 queued | `api/routes/tools.py` job admission | 429 |
| Artifact size | 50 MiB (= `SPLUNK_BOUNDARY_MAX_BYTES`) | `services/artifact_guard.py` | 413 |
| Paste payload | 5 MiB raw text | `api/routes/notables.py` `paste_notable` entry | 413 |
| Ollama-backed POSTs | 30/min per client IP | rate-limit middleware in `api/main.py` | 429 |
```

Commit `docs: hardening limits scoping`.

**C2.1.x — one commit per limit** (5 commits). Each: constant at the named site,
enforcement, one test in `tests/test_investigation_fixes.py`, §0 gates.
- C2.1.1 timeout — run the ollama call via `concurrent.futures.ThreadPoolExecutor` and
  `future.result(timeout=ANALYZE_TIMEOUT_S)`; on `TimeoutError` raise `HTTPException(504)`
  BEFORE any persist. Test `test_analyze_timeout_504` (stub ollama sleeps > timeout —
  monkeypatch the timeout constant to 0.1s so the test stays fast).
- C2.1.2 job cap — before enqueueing in the `POST /api/execute` handler, count queued jobs;
  `len(queued) >= JOB_QUEUE_MAX` → `HTTPException(429)`. Test `test_job_queue_cap_429`.
- C2.1.3 artifact cap — in `services/artifact_guard.py` validation add size check against
  `SPLUNK_BOUNDARY_MAX_BYTES` → returns rejected report (fail-closed contract preserved).
  Test `test_artifact_size_cap`.
- C2.1.4 paste cap — first lines of `paste_notable`:
  `if len(request.raw_text or "") > PASTE_MAX_BYTES: raise HTTPException(413)`.
  Test `test_paste_payload_cap_413`.
- C2.1.5 rate limit — middleware in `api/main.py` before the auth gate:

  ```python
  _RATE_HITS: dict = {}  # ip -> [timestamps]
  RATE_LIMIT_PER_MIN = 30
  _RATE_PATHS = ("/api/db/analyze", "/api/analyses", "/api/splunk/search-one",
                 "/api/db/supportive-queries/draft", "/api/code-review")

  @app.middleware("http")
  async def _ollama_rate_limit(request, call_next):
      if request.method == "POST" and any(request.url.path.startswith(p) for p in _RATE_PATHS):
          ip = request.client.host if request.client else "?"
          now = time.time()
          hits = [t for t in _RATE_HITS.get(ip, []) if now - t < 60.0]
          if len(hits) >= RATE_LIMIT_PER_MIN:
              _RATE_HITS[ip] = hits
              return JSONResponse({"detail": "Too many requests"}, status_code=429)
          hits.append(now)
          _RATE_HITS[ip] = hits
      return await call_next(request)
  ```

  Test `test_rate_limit_429` (monkeypatch `RATE_LIMIT_PER_MIN` to 2, hit analyze thrice,
  third → 429; clear `_RATE_HITS` in the fixture).

---

## Stage C3 — Multi-model per-stage choice (P2) `[mechanical]`

Stage vocabulary is verified: `api/routes/analyze.py` knows `analysis_stage` values
`"initial"` and `"follow_up"` (the UI sends `priorAnalysisText ? 'follow_up' : 'initial'` —
`web/modules/analysis.js:1079`). Override keys: `initial`, `follow_up`, `closure`.

**C3.1 — API.** `api/schemas.py` `AnalyzeRequest` (line ~62) — add one field:

```python
    stage_models: Optional[Dict[str, str]] = None  # per-stage override: initial|follow_up|closure
```

(add `from typing import Dict, Optional` if the file lacks it). `api/routes/analyze.py`
lines 58-63 — replace:

```python
        case_id = request.case_id
        model = request.model
        context = request.context
```

with:

```python
        case_id = request.case_id
        context = request.context
        # C3: per-stage model override; missing/empty entry falls back to
        # request.model ("" keeps the auto-resolve-an-installed-model behavior).
        model = (request.stage_models or {}).get(requested_analysis_stage) or request.model
```

and MOVE the `requested_analysis_stage = ...` assignment (currently line 61) ABOVE the new
`model =` line (the exact 6-line block to rearrange is at `api/routes/analyze.py:58-63`).
Optional C3.1.x: `grep -n "model" api/routes/closure.py` — if the closure-note handler
threads a model, honor `stage_models.get("closure")` the same way; if it does not, leave a
one-line comment `# stage_models["closure"] reserved` and skip.

**C3.2 — UI.**
- `web/app.modular.js` (line ~80, next to `analysisModel: ''`): add
  `stageModels: { initial: '', follow_up: '', closure: '' },`.
- `web/modules/analysis.js` — both `API.analyze({…})` payloads (lines ~936-942 and
  ~1074-1081): add `stage_models: this.stageModels || {},` after the `model:` line.
- `web/components/AnalysisTab.js`: add prop `'stageModels'` (props list ~line 11) and emit
  `'update:stage-models'` (emits list ~line 65). In the Stage 4 and Stage 5 panels add a
  model select mirroring the Stage 3 pattern (that select is at ~line 735, bound
  `:value="analysisModel" @input="$emit('update:analysis-model', …)"`):

  ```html
  <select
      :value="(stageModels && stageModels.follow_up) || ''"
      @input="$emit('update:stage-models', { ...(stageModels || {}), follow_up: $event.target.value })"
      class="…same classes as the Stage 3 model select…">
      <option value="">Default model</option>
      <option v-for="m in ollamaHealth.models" :key="m" :value="m">{{ m }}</option>
  </select>
  ```

  (Stage 5 uses key `closure`.) At the `<analysis-tab>` binding site
  (`grep -n "analysis-model" web/index.modular.html web/app.modular.js` to find it) add
  `v-model:stage-models="stageModels"`.
- Cache-busters: `api.js` untouched; bump `app.modular.js` (v19→v20), `analysis.js`
  (v19→v20), `AnalysisTab.js` (v19→v20) in `web/index.modular.html`.

**C3.3 — Tests.** `tests/test_api_analyze_flow.py` — new class `TestAnalyzeStageModels`
(arrange block copied from that file's simplest analyze test; the fake Ollama stub records
the `model` kwarg): `test_initial_stage_uses_override`, `test_follow_up_stage_uses_override`,
`test_missing_key_falls_back_to_model`. Harness: extend the `stepper_guards` scenario file
with `analyze_stage_models` asserting the POST body carries `stage_models` (stub the same
way `save_supportive` stubs `axios.post`).

**C3.4 —** §0 gates (incl. now-24 scenarios), commit
`feat: per-stage model selection in the analysis wizard`, push, CI green.

---

## Stage C4 — Paste-box storage via boundary batch (P2) `[mechanical]`

**DESIGN CALL (recorded Sept 30 — do not re-litigate): the "paste-batch adapter".**
Verified from source: `ingest_json_notables` lives in `services/splunk_boundary.py:364` and
its docstring says the paste-box flow deliberately REMAINS the analyst path — so do NOT
merge the flows. Instead give the paste path the same batch bookkeeping the boundary
already has: `quarantine_file` → `{batch_id}-manifest.json` → `ingest_manifest` records
`inserted_ids` + `ingest_window` → `purge_batch` deletes staged file + manifest + DB rows
by `inserted_ids` (fallback: window). **The manifest is the linkage — no model changes.**
Paste is an operator-facing admission (`admit_text`), so it satisfies the quarantined-mode
rule "data may only enter via the boundary" and stays allowed in `quarantined` mode.

**C4.1 — `admit_text` in `services/splunk_boundary.py`.** New function next to
`quarantine_file` (~line 168), same contract (fail-closed validation: `.txt` extension,
`max_bytes()` cap, binary sniff — reuse `validate_file` semantics on a temp file), staging
name `{batch_id}-pasted.txt`, manifest fields identical to `quarantine_file`'s plus
`"source": "paste-box"`. Public: `admit_text(text: str, source_label: str = "paste-box") -> Dict`.

**C4.2 — wire `paste_notable` (`api/routes/notables.py` ~1272).** As the FIRST action after
the empty-check, call `admit_text(raw_text)`; keep the ENTIRE existing pipeline byte-for-byte
(`split_pasted_notables`, `sanitize_logs_with_tokens`, `sanitize_pii_phi`, dedup, inserts).
After the inserts commit, write into the manifest exactly as `ingest_manifest` does:
`manifest["ingest"] = {"rows_inserted": …, "inserted_ids": [ids of rows created by THIS paste]}`,
`manifest["ingest_window"]`, rewrite `{batch_id}-manifest.json`. Include `batch_id` in the
route's response payload (`"batch_id": …`) so the UI/test can reference it.

**C4.3 — Tests** in `tests/test_investigation_fixes.py`:
- `test_paste_creates_boundary_manifest` (paste → `list_batches()` shows source `paste-box`,
  `inserted_ids` non-empty),
- `test_purge_batch_removes_pasted_rows` (`purge_batch(batch_id)` → those SplunkEvent ids
  gone, staged file + manifest gone),
- `test_paste_sanitization_byte_identical` (golden: same input through old entry helper vs
  new path → identical stored `raw`),
- `test_paste_dedup_unchanged` (same paste twice → second is skipped),
- `test_paste_allowed_in_quarantined_mode` (default mode admits via `admit_text`).

**C4.4 —** §0 gates, commit
`feat: paste-box storage through boundary batches (manifest/purge parity)`, push, CI green.

---

## Stage D1 — Vercel handoff doc (P3 · assemble now, unblocks by conversation) `[mechanical]`

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

## Stage D2 — Live Splunk rehearsal checklist (P3 · assemble now) `[mechanical]`

**D2.1 — Create `docs/SPLUNK_REHEARSAL.md`:** numbered rehearsal: (1) get `SPLUNK_URL` +
`SPLUNK_TOKEN` (read-only role); (2) `export SEARCH_BACKEND=splunk SPLUNK_URL=… SPLUNK_TOKEN=…`
and restart via `scripts/start`; (3) run one Stage-2 query via the UI "Run in Splunk";
(4) verify: CSV `_time` epoch → ISO normalization, stats-row shape, bearer header, boundary
latch interplay (`services/splunk_boundary.py` — QUARANTINED latch must not block read
searches); (5) failure drill: bad token → `SplunkSearchError` surfaces as `query_failed`
card status. Each step gets an expected-result line.
**D2.2 —** Gates (docs-only), commit `docs: live Splunk rehearsal checklist (D2)`, push, CI green.

---

## Stage D3 — User-side checklist (not automatable — record only) `[informational]`

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
| C1 | Plan signed off → build stages C1B.1–C1B.4 land with the auth matrix green |
| C2 | Defaults signed off → each limit lands with its named regression test |
| C3 | Per-stage model overrides reach the Ollama stub; fallback proven |
| C4 | Manifest/purge parity + byte-identical sanitization proven |
| D1–D2 | Docs assembled so unblocking = one conversation/checklist run |
