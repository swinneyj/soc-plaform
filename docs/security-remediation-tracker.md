# Security Remediation Tracker — SOC Platform (`soc-plaform`)

**Prepared:** Sept 16, 2026 · **Repo:** `swinneyj/soc-plaform` (public) · **Branch:** `dev-dalton`
**Updated:** Sept 25, 2026 — added environment notes (E1/E2) + finding #14; synced item statuses with commits `1775c10` (Downloads) / `be4c644` (`~/soc-platform`)
**Updated (session 2, Sept 25):** #15 fixed & deployed-verified; Python pinned 3.14; frontend auth wiring (config-only activation); Splunk boundary ("the latch") architecture landed — see "Architecture hardening" below. Suite is now 121/121 on both Python 3.14 and 3.9. All work lives on `origin/dev-dalton` in `~/soc-platform` (commits `00c7e69`…`908b6d5`).
**Scope:** code/`git grep` inspection of the working copy at `~/Downloads/soc-plaform-main`. Not a pen-test — a prioritized list of concrete, actionable findings.

---

## Priority 1 — Do first

### 1. Rotate the exposed shared Neon password 🔄 ROTATION DONE — DISTRIBUTION OPEN
- **What:** The shared Neon `DATABASE_URL` (with password `npg_...`) was pasted into a terminal/chat in plain text. Coworker is already rotating.
- **Status Sept 25:** coworker completed the rotation. Remaining on Dalton's side:
- **Remediation:**
  - [x] Coworker resets password in Neon console (Roles → `neondb_owner` → Reset password)
  - [ ] Update `.env` locally with new URL (currently pointing at local Postgres on 5432)
  - [ ] Update Vercel env vars if the production deploy uses this DB — otherwise deploys break
  - [ ] Verify no other copies exist (terminal history, notes, shared docs, password manager entries, old `.env` backups)
- **Owner:** coworker (done) + Dalton (.env/Vercel)

### 2. `.env` had a broken duplicate `DATABASE_URL=` ✅ FIXED today
- **What:** A stray empty `DATABASE_URL=` line (leftover from an interrupted `read -r -s` prompt) was overriding the real value, causing 500s on `/api/db/notables`.
- **Remediation:** Removed today. A commented template line for the new Neon URL is in place. **Verify after rotation that the real URL lands on an uncommented line.**

### 3. Local Postgres password lives in plaintext in `.env`
- **What:** `POSTGRES_PASSWORD=...` in `.env`. Not committed to git (confirmed clean), but it's on disk in plaintext and gets passed around manually between teammates.
- **Remediation:**
  - [x] Confirm `.env` is in `.gitignore` (it is — `.gitignore:6`)
  - [ ] Consider macOS Keychain / direnv / 1Password CLI instead of a plain shared file — the manual handoff pattern is how #1 happened

### 4. Unprotected DB dump in `~/Downloads`
- **What:** `current_soc_platform_dump.sql` sits in `~/Downloads` (a folder that may sync to iCloud). Repo is public; `.gitignore` blocks `*.sql` — good — but the dump itself may contain sensitive data sitting in an unencrypted folder.
- **Remediation:**
  - [ ] Inspect the dump for credentials/PII
  - [ ] Move to `local-backups/` (gitignored) or delete if not needed
  - [ ] Confirm never committed: `git log --all --oneline -- '**/*.sql'`

---

## Priority 2 — This week

### 5. No authentication on the API
- **What:** `api/main.py` has no auth middleware, no API key check, no login. Anyone who can reach the server can hit every endpoint (triage data, notables, job execution).
- **Why it matters:** The Vercel deployment (`soc-plaform-livid.vercel.app`) is publicly reachable; if its backend talks to the shared DB with no auth, anyone can query or mutate SOC data.
- **Remediation:**
  - [ ] Add an API-key/token dependency (FastAPI `Depends`) on all non-health endpoints, or
  - [ ] Restrict exposure (Vercel password protection / IP allowlist) while internal-only
- **Note:** biggest structural gap — deserves a deliberate design conversation, not a quick patch.
- **Progress Sept 25:** API-key scaffold landed — `require_api_key` dependency on the dangerous routes (execute/regression/jobs-delete); no-op until `API_KEY` is set, so local dev is unchanged.
- **Progress Sept 25 (session 2):** activation is now **config-only** — the frontend ships `web/utils/auth.js` (single axios interceptor stamps `X-API-Key` from `SOC_CONFIG.apiKey`/`?apiKey=` across all 67 call sites; no-op when unset), `X-API-Key` is CORS-allowlisted with a behavioral preflight test, and the boundary write endpoints carry the gate too. **To activate:** set `API_KEY` (Vercel) + get the key into the browser (`SOC_CONFIG.apiKey` injection — the one remaining design choice, needs a ~10-min coworker conversation since static pages can't read env vars at runtime).

### 6. Unauthenticated job-execution endpoints
- **What:** `/api/jobs/*` and tool-execution routes spawn processes server-side (`tool_runs`). Combined with #5, this is remote code execution exposed to whoever can reach the server.
- **Remediation:** Gate behind auth before exposure beyond localhost.
- **Progress Sept 25:** covered by the same `require_api_key` dependency (remains no-op until `API_KEY` is set — natural fit for BWS-injected config later).

### 7. Wildcard HTTP methods/headers in CORS
- **What:** `CORSMiddleware(allow_methods=["*"], allow_headers=["*"])` — origins are allowlisted (good), methods/headers are wide open.
- **Remediation:**
  - [x] Tighten to the explicit methods the frontend uses (e.g. `["GET","POST","DELETE","PUT"]`) and enumerate headers — done Sept 25 (`GET/POST/PUT/DELETE`, headers `Content-Type`/`Accept`)
  - [x] Add `X-API-Key` to `allow_headers` (needed for browser auth) with a preflight regression test — Sept 25, `7863a74`
- **Effort:** small.

### 8. `.env.example` contains `POSTGRES_PASSWORD=theitguru`
- **What:** Tracked, committed, public. Looks like a real-ish password (possibly reused elsewhere).
- **Remediation:**
  - [x] Change to a clearly fake placeholder: `replace-with-a-long-random-password` — done Sept 25 (old value remains in git history, which is expected for a placeholder)
  - [ ] If "theitguru" is/was a real password anywhere, rotate it there too
- **Effort:** one-line edit.

---

### 14. FastAPI docs & OpenAPI schema publicly exposed ✅ FIXED
- **What:** `/docs` (Swagger UI) and `/openapi.json` answered 200 unauthenticated — a complete recon map of every endpoint for anyone scanning the Vercel deployment.
- **Remediation:** docs/openapi URLs are now `None` unless `ENABLE_DOCS=1` is set; landed Sept 25 in commit `be4c644`.

### 15. Path traversal in `/api/reports/{report_name}` — unauthenticated arbitrary file read ✅ FIXED
- **What:** `os.path.join(get_reports_dir(), report_name)` discards the base directory when `report_name` is absolute, and `..` segments were never checked. `GET /api/reports/%2Fetc%2Fpasswd` (or `%2F…%2F.env`) served any file the process could read — live on the public deployment, no auth required. Strictly worse than #14: a code bug, not a config gap.
- **Remediation:** resolve candidate against the Reports root and require it to stay inside that root (symlinks included), then require `is_file()` — the same pattern the adjacent `/api/tool-artifacts` endpoint already used. Landed Sept 25 in commit `7c0ecad` with 6 regression tests (traversal / absolute / nested-escape / symlink-escape / missing / legitimate-download). Suite: 74/74.

---

## Priority 3 — Hygiene / next sprint

### 9. Python 3.9 venv (EOL) ✅ RESOLVED
- **What:** Local `.venv` runs Python 3.9.6 (macOS CommandLineTools system Python). 3.9 is end-of-life.
- **Status Sept 25 (session 2):** resolved. `.python-version` pins `3.14`, Dockerfile stages moved to `python:3.14-slim`, `CONTAINERIZATION.md` aligned (`00c7e69`). `.venv314` (3.14.7) runs the full suite; the old 3.9 venv also still passes, verified both ways. Docker build not verifiable on this machine (no docker CLI) — first `docker compose build` should be watched.
- **Follow-up:** 3.14 surfaced deprecation warnings (SQLAlchemy/`datetime.utcnow()` call sites ~`api/main.py:3339`; Starlette prefers `httpx2` for TestClient) — small cleanup ticket before a future Python removes them.

### 10. Pip is ancient in the venv ✅ RESOLVED
- **What:** pip 21.2.4 flagged during install.
- **Status Sept 25:** resolved — `.venv314` ships modern pip; suite green.

### 11. Dependency audit / Dependabot
- **What:** No evidence of `pip-audit` or Dependabot.
- **Remediation:**
  - [ ] Run `pip-audit` against `requirements.txt`
  - [ ] Enable GitHub Dependabot alerts (Settings → Security → Dependabot) — free, ~2 minutes
- **Watch list:** `fastapi`, `starlette`, `uvicorn`, `cryptography`, `sqlalchemy`.

### 12. `triage.db` (SQLite) in working tree
- **What:** Legacy SQLite DB, gitignored. Runtime no longer uses it (per `db/models.py`), but if it holds real triage data it's another sensitive file on disk.
- **Remediation:** [x] **Resolved Sep 25, 2026 — archived** to `splunk-es-backup-toolkit/triage.db` (the exact path `RUNBOOK.md`'s restore procedure expects). Audit before moving: all-TEST simulated data (7 triage results, 12 Splunk events, rest empty), SQLite `integrity_check: ok`. Working tree is now clear of it; binary stays gitignored as a provided artifact.

### 13. CORS_ORIGINS only has the Vercel prod URL
- **What:** Local dev UI may not be in the allowlist for local testing against a remote backend.
- **Remediation:** Keep prod strict; use a local-only `CORS_ORIGINS` value in `.env` if needed. Never add `localhost` to deployed config.

---

## Environment notes — workstation findings (Sept 25)

### E1. Hidden always-on API server via LaunchAgent ✅ MITIGATED
- **What:** `~/Library/LaunchAgents/local.soc-platform.api.plist` ran uvicorn from `~/soc-platform/scripts/start_soc_api.sh` with `RunAtLoad: true` + `KeepAlive: true` — auto-starts at every login and **respawns within ~30s of being killed**.
- **Why it matters:** an always-on, unauthenticated API instance that never appears in the Dock, survives manual kills, and silently serves whatever code sits in `~/soc-platform`. It was the root cause of the recurring `address already in use` error on port 8000, and — combined with #5/#6 — an unattended public-exposure surface nobody was accounting for.
- **Status:** unloaded and disabled Sept 25 (`launchctl bootout gui/501/local.soc-platform.api` + `launchctl disable gui/501/local.soc-platform.api`). The plist remains on disk; verified no respawn after its restart window.
- **Re-enable if ever wanted:**
  ```bash
  launchctl enable gui/$(id -u)/local.soc-platform.api
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.soc-platform.api.plist
  ```
- **Follow-ups:**
  - [ ] Decide whether auto-start should exist at all once BWS lands — preferred pattern is a `bws run` launcher started deliberately, not a login service
  - [ ] Note for the record: `sh.brew.postgresql@16.plist` also auto-starts (Homebrew Postgres, localhost-only — low risk, documented here so the inventory is complete)

### E2. Generic USB mouse orphaned from macOS settings *(workstation note — no direct security impact)*
- **What:** The no-name `" USB OPTICAL MOUSE"` binds to the generic `AppleUserHIDEventDriver`, and a live flip test (`com.apple.swipescrolldirection` true↔false with zero behavioral change) proved it ignores macOS's scroll-direction pipeline entirely; its cheap encoder also double-fires detents.
- **Why it's in this tracker:** it documents that machine-level oddities here aren't app bugs — and the same session surfaced the genuinely security-relevant item: the old Neon URL (pre-rotation password included) sat in a Safari tab's URL bar and persists in browser history. Credential-in-URL is exposure even after rotation.
- **Mitigation:** Mos 4.2.1 installed (scroll reversal + smoothing), added as hidden login item.
- **Follow-ups:**
  - [ ] Clear Safari history entries containing the old Neon URL (History → Show All History → search "neon" → delete)
  - [ ] Consider replacing the mouse — encoder jitter is hardware; Mos only masks it

---

## Architecture hardening — the Splunk boundary ("the latch") — Sept 25

**Motivation (Dalton's requirement):** Splunk is the sensitive system of record; importing/exporting between it and this platform needed one detachable, inspectable unit instead of data-loading paths accreting across tools.

**What exists now** (commits `a216ca7` → `908b6d5`, all on `origin/dev-dalton`):

- **`services/splunk_boundary.py`** — the single choke point for Splunk-derived data:
  - **Mode latch** (`SPLUNK_BOUNDARY_MODE`): `quarantined` (default; HTTP admission 403s — data enters only via host-local tooling), `restricted` (HTTP admission allowed behind the API-key gate), `open` (report-only validation). Flipping the latch is an explicit config change.
  - **Fail-closed validation**: extension allowlist (`.csv/.json`), size cap (`SPLUNK_BOUNDARY_MAX_BYTES`, 50 MiB), binary sniff, basename-only paths.
  - **Quarantine staging + batch manifests**: every admission copies the file into `Data/quarantine/` under a batch id with a JSON manifest (origin, validation report, ingest stats, ingest window).
  - **Batch purge**: `purge_batch()` removes staged files *and* the ingested DB rows — un-ingestion without touching Splunk.
  - **Canonical engines**: `ingest_csv_events` (shared field mapping, event-level dedup, truncation, tz-normalizing timestamps) and `ingest_json_notables` (full-payload preservation, epoch/ISO timestamps, payload-hash dedup for same-second events). Both carry a total-failure guard so a broken DB can never report success.
- **Every ingest path routed through it**: `splunk_csv_ingestor` CLI, `splunk_folder_watcher` CLI (its duplicated engine deleted; invalid files refused AND left un-archived for the operator), and the mode-gated HTTP endpoints (`GET /api/splunk-boundary/status`, `POST .../admit`, `DELETE .../batches/{id}` — writes behind `require_api_key`).
- **UI widget** (`web/components/SplunkBoundaryWidget.js`): mode badge, batch table, per-batch purge with inline confirmation — visible at the top of the modular UI.
- **Verified end-to-end**: real CLIs → real DB (ingest → dedup rerun → 0 new; binary CSV refused + un-archived; JSON notables stored with full payloads; manifests + purge round-trips). Also verified live on the Vercel deployment: `/docs` 404, `/openapi.json` 404, traversal probes 404, health 200.

**Security dividends:** the deploy can be latched shut with one env var; all Splunk data is batch-attributable and purgable; HTTP admission is off by default; the auth gap on boundary endpoints (found while building the widget) is closed with a regression test.

**Mock Splunk backend** (`services/search_backend.py`, `a216ca7`): pluggable `SearchBackend` — deterministic mock (honest SPL subset, committed seed corpus, three ground-truth scenarios) + loud-failure real-Splunk stub + `SEARCH_BACKEND` factory. Phase 3 develops with **zero Splunk credentials in the environment** — itself a posture improvement, and the future REST connector's credentials will live inside the boundary, not beside it.

**Remaining boundary work (deliberate, not urgent):**
- [ ] Wire `SOC_CONFIG.apiKey` into the served pages (Vercel deploy-time injection) — unlocks `restricted` mode + full-gate activation
- [ ] Consider routing the paste-box flow's *storage* through a boundary batch for manifest/purge parity (sanitization must stay — see `ingest_json_notables` docstring)

---

## Checked and found OK ✅
- `.env` is gitignored (`.gitignore:6`)
- No `npg_` values or Neon hostnames appear in any tracked file
- Local Postgres password is not committed anywhere in git history
- `*.sql` / `*.db` / `*.sqlite` are gitignored
- CORS origins are an explicit allowlist, not `*`
- `allow_credentials=False` (reduces CSRF-style risk)
- Every push secret-scanned before it left the machine (pattern: `npg_*` + password/token/secret assignments)
- Boundary validation, latch modes, purge, and both ingest engines are unit-tested hermetically (sqlite-injected) — suite 121/121 on Python 3.14 and 3.9

## Open items, in order
1. **BWS access token** (coworker) → then: verify secret names, build the `bws run` launcher, slim `.env` to just the token
2. **New Neon URL distribution** → `.env` + **Vercel env vars** (the classic miss that breaks the next deploy)
3. **Auth activation decision** (coworker conversation) → `API_KEY` in Vercel + `SOC_CONFIG.apiKey` injection in the pages
4. **Dependabot alerts** (GitHub Settings → Security, ~2 min, you or Justin) + one-time `pip-audit`
5. **Deprecation cleanup** from the 3.14 warnings (`utcnow()` → timezone-aware; `httpx2`)
6. **Small decisions**: `triage.db` keep/delete; Safari history cleanup for the old Neon URL
