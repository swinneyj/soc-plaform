# Security Remediation Tracker — SOC Platform (`soc-plaform`)

**Prepared:** Sept 16, 2026 · **Repo:** `swinneyj/soc-plaform` (public) · **Branch:** `dev-dalton`
**Updated:** Sept 25, 2026 — added environment notes (E1/E2) + finding #14; synced item statuses with commits `1775c10` (Downloads) / `be4c644` (`~/soc-platform`)
**Updated (session 2, Sept 25):** #15 fixed & deployed-verified; Python pinned 3.14; frontend auth wiring (config-only activation); Splunk boundary ("the latch") architecture landed — see "Architecture hardening" below. Suite is now 121/121 on both Python 3.14 and 3.9. All work lives on `origin/dev-dalton` in `~/soc-platform` (commits `00c7e69`…`908b6d5`).
**Updated Sept 30:** owner closed the mouse (E2) + exposure-restriction (#5) sub-item; **Vercel deployment retired** (owner decision) — `deploy.yml` deleted, `docs/VERCEL_HANDOFF.md` stubbed to a retirement notice; the Vercel project itself is deliberately **kept on ice** (frozen, undeployable, retained in case circumstances change).
**Updated 2026-10-05:** added the admission-hardening entries (C2.1.2 job-queue cap, C2.1.4 paste cap, C2.1.6 tool-subprocess env isolation, C4 paste-batch) with their regression-test names, and the API-security-review remediations (F1 generic 500s + request-id server-side logging, F5 constant-time login + per-IP/per-username throttling).
**Updated Sept 28:** added E3 — AI agent harness (Freebuff Desktop) workstation exposure; canonical risk notes + operating rules live in `AGENTS.md` → "Freebuff Desktop risk notes". Also this session: verified the git restore path (0.040s, sha256-identical) and cleared a stale `postmaster.pid` after unclean shutdown (see AGENTS.md machine quirks).
**Scope:** code/`git grep` inspection of the working copy at `~/Downloads/soc-plaform-main`. Not a pen-test — a prioritized list of concrete, actionable findings.

---

## Priority 1 — Do first

### 1. Rotate the exposed shared Neon password ✅ ROTATION + LOCAL CUTOVER DONE — VERCEL LEG RETIRED (SEPT 30)
- **What:** The shared Neon `DATABASE_URL` (with password `npg_...`) was pasted into a terminal/chat in plain text. Coworker is already rotating.
- **Status Sept 28:** cutover executed and verified end to end on Dalton's machine:
  - [x] Coworker resets password in Neon console (Roles → `neondb_owner` → Reset password)
  - [x] `.env` updated via BWS (`scripts/pull-secrets` regenerates from vault; values never transit chat)
  - [x] **Vercel env vars — moot (Sept 30):** the hosted deployment was retired (owner decision) — `deploy.yml` deleted, no branch deploys anymore. Project kept on ice by owner decision (`docs/VERCEL_HANDOFF.md`)
  - [x] No-other-copies sweep (Sept 28): tracked files clean; git history clean (`git log --all -S neon.tech` = zero commits); `.env.example`/`.env.bws.template` placeholder-only; old `~/soc-platform/.env` clean; Downloads dump clean. Residual dead-URL copies (inert, old password) **scrubbed same day**: 1 line in `~/.zsh_history` + 3 lines across 3 `~/.zsh_sessions` files (Sept 25 era) — targeted line deletion, backups in `/tmp/neon-scrub-backup/`, zero neon.tech mentions remain in shell history. Safari History.db per E2 (manual in-app cleanup, still open — old credential inert, hygiene only).
- **Verification (Sept 28):** connection smoke passed (PostgreSQL 18.6, `neondb`, 11 tables, 7 triage rows); API restarted against Neon via `scripts/dev`; **write-path roundtrip passed** (evidence POST → id 8 → GET → DELETE → net-zero rows) — live INSERT/SELECT/DELETE confirmed against the cloud DB.
- **Incident note (stale-URL first pull):** the first BWS-stored URL failed auth (`password authentication failed for neondb_owner`) despite clean structure — it was a pre-rotation string (old password, live address). Fixed by re-copying a fresh connection string from the Neon console into BWS. Lesson: connection strings must come from the console copy button at handoff time, never from prior messages/history — same rule as the original incident, now with a concrete second example.

### 2. `.env` had a broken duplicate `DATABASE_URL=` ✅ FIXED today
- **What:** A stray empty `DATABASE_URL=` line (leftover from an interrupted `read -r -s` prompt) was overriding the real value, causing 500s on `/api/db/notables`.
- **Remediation:** Removed today. A commented template line for the new Neon URL is in place. **Verify after rotation that the real URL lands on an uncommented line.**

### 3. Local Postgres password lives in plaintext in `.env` ✅ FIXED (Sept 28 — macOS Keychain)
- **What:** `POSTGRES_PASSWORD=...` in `.env`. Not committed to git (confirmed clean), but it's on disk in plaintext and gets passed around manually between teammates.
- **Remediation:**
  - [x] Confirm `.env` is in `.gitignore` (it is — `.gitignore:6`)
  - [x] macOS Keychain instead of a plain shared file — Sept 28: `scripts/secrets-keychain` stores the credential-bearing keys (`DATABASE_URL`, `API_KEY`; `POSTGRES_PASSWORD`/`COMPOSE_DATABASE_URL` covered defensively) in the login keychain (service `soc-platform`). `.env` keeps `# NAME=@keychain` markers + the BWS bootstrap token + non-secret config only. `scripts/start`/`scripts/dev` inject keychain values (explicit env wins; BWS-injected values win at launch); `scripts/pull-secrets` re-migrates after every pull (idempotent). Verified live: digest round-trip, explicit-override precedence, keychain-only DB connect (11 tables on Neon), full restart via keychain path. Note: Docker compose interpolation reads `.env` directly — export from the keychain first if a compose flow returns (see script header).

### 4. Unprotected DB dump in `~/Downloads` ✅ FIXED (Sept 28)
- **What:** `current_soc_platform_dump.sql` sits in `~/Downloads` (a folder that may sync to iCloud). Repo is public; `.gitignore` blocks `*.sql` — good — but the dump itself may contain sensitive data sitting in an unencrypted folder.
- **Remediation (all done Sept 28):**
  - [x] Inspected the dump (991 lines, Sep 11 pre-Neon schema): zero credential-pattern hits (`npg_`/private keys/tokens), the 9 "password"-word lines are playbook/query text (e.g. password-spraying checks) + `TEST-POWERSHELL-1`/`TEST-PINGFED-1` rows, and all 6 emails are `@corp.internal` synthetic corpus — no real PII
  - [x] Moved to `local-backups/` (gitignored — `git check-ignore` confirms)
  - [x] Confirmed never committed: `git log --all -- '*.sql'` = empty

---

## Priority 2 — This week

### 5. No authentication on the API
- **What:** `api/main.py` has no auth middleware, no API key check, no login. Anyone who can reach the server can hit every endpoint (triage data, notables, job execution).
- **Why it matters:** The Vercel deployment (`soc-plaform-livid.vercel.app`) is publicly reachable; if its backend talks to the shared DB with no auth, anyone can query or mutate SOC data.
- **Remediation:**
  - [x] Add an API-key/token dependency (FastAPI `Depends`) on all non-health endpoints (covered by the method-based mutation middleware + A1 path-aware gate), or
  - [x] Restrict exposure (Vercel password protection / IP allowlist) while internal-only — owner confirmed the current posture is acceptable (Sept 30, 2026)
- **Note:** biggest structural gap — deserves a deliberate design conversation, not a quick patch.
- **Progress Sept 25:** API-key scaffold landed — `require_api_key` dependency on the dangerous routes (execute/regression/jobs-delete); no-op until `API_KEY` is set, so local dev is unchanged.
- **Progress Sept 25 (session 2):** activation is now **config-only** — the frontend ships `web/utils/auth.js` (single axios interceptor stamps `X-API-Key` from `SOC_CONFIG.apiKey`/`?apiKey=` across all 67 call sites; no-op when unset), `X-API-Key` is CORS-allowlisted with a behavioral preflight test, and the boundary write endpoints carry the gate too. **To activate:** set `API_KEY` (Vercel) + get the key into the browser (`SOC_CONFIG.apiKey` injection — the one remaining design choice, needs a ~10-min coworker conversation since static pages can't read env vars at runtime). **Resolved Sept 30:** the injection design decision is made — server-side injection at serve time.

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
  - [x] Sept 28: verified "theitguru" is not a live password anywhere reachable — SCRAM-SHA-256 hash of the only password-bearing local role (`soc_platform`) does not match it (local PG is `trust`-auth anyway), no `mini`/docker-PG role exists, all 4 BWS vault values differ, no `.env*` in either checkout contains it, zero shell-history hits. It survives only as the documented placeholder in docs + git history. **Human residual:** if it was ever reused as a real password outside this platform (other services/accounts), rotate there — outside this repo's reach.
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
- **Follow-up:** 3.14 surfaced deprecation warnings (SQLAlchemy/`datetime.utcnow()` call sites ~`api/main.py:3339`; Starlette prefers `httpx2` for TestClient) — small cleanup ticket before a future Python removes them. **utcnow() portion resolved Sept 28, 2026** — see open item #5.

### 10. Pip is ancient in the venv ✅ RESOLVED
- **What:** pip 21.2.4 flagged during install.
- **Status Sept 25:** resolved — `.venv314` ships modern pip; suite green.

### 11. Dependency audit / Dependabot
- **What:** No evidence of `pip-audit` or Dependabot.
- **Remediation:**
  - [x] **Done Sept 30 (stage A2):** pip-audit clean on the production (>=3.10) branch; the 3.9-branch advisories are recorded accepted risk (see the requirements.txt header).
  - [x] **Done Sept 30 (~18:45):** Dependabot alerts/config live — first weekly run opened 7 update PRs (#1–#7). Pending human review: merge the pip bumps + action bumps, mind the dual-runtime pins (Dependabot's `
` targets the py3.10 line only; verify the `<3.10` pin still satisfies the advisories before merging).
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
  - [x] **Decision (Sept 28): no login auto-start.** BWS has landed (token + CLI live, `scripts/start` runs the API via `bws run`), and the deliberate-launcher pattern is confirmed as the way: `scripts/start` / the Desktop icon start everything on demand, `scripts/start --stop` stops it. The retired LaunchAgent plist stays on disk (disabled) purely as a documented escape hatch — do not re-enable without a fresh decision.
  - [x] Noted for the record: `sh.brew.postgresql@16.plist` also auto-starts (Homebrew Postgres, localhost-only — low risk, kept in the inventory)

### E2. Generic USB mouse orphaned from macOS settings *(workstation note — no direct security impact)*
- **What:** The no-name `" USB OPTICAL MOUSE"` binds to the generic `AppleUserHIDEventDriver`, and a live flip test (`com.apple.swipescrolldirection` true↔false with zero behavioral change) proved it ignores macOS's scroll-direction pipeline entirely; its cheap encoder also double-fires detents.
- **Why it's in this tracker:** it documents that machine-level oddities here aren't app bugs — and the same session surfaced the genuinely security-relevant item: the old Neon URL (pre-rotation password included) sat in a Safari tab's URL bar and persists in browser history. Credential-in-URL is exposure even after rotation.
- **Mitigation:** Mos 4.2.1 installed (scroll reversal + smoothing), added as hidden login item.
- **Follow-ups:**
  - [ ] Clear Safari history entries containing the old Neon URL (History → Show All History → search "neon" → delete)
  - [x] Consider replacing the mouse — owner confirmed the mouse is fixed (Sept 30, 2026)

### E3. AI agent harness: Freebuff Desktop on this workstation (Sept 28)
- **What:** Freebuff Desktop (`com.freebuff.desktop`, Electron, auto-updating) is the coding-agent harness in use on this repo. Vendor: Freebuff, Inc. (YC F24, ~4 people) — **ad-funded**: prompts/messages may be analyzed to personalize ads, chat threads are retained **server-side indefinitely** (until a deletion request completes; support@codebuff.com), and device fingerprinting is part of auth.
- **Why it's in this tracker:** an agent harness with filesystem + terminal access to this checkout, run by a vendor whose open tracker carries unfixed security issues (#1146 out-of-scope `rm -rf` deletion with no confirmation gate; #1231 unmerged XSS fix; #1306 MCP tool parameters dropped). Combined with the unauthenticated-API findings (#5/#6), it widens the local exposure surface.
- **Canonical notes:** full risk summary, tracker citations with check-dates, and the binding operating rules (keep the tree committed; review destructive commands; no MCP connectors; never the Space Bunny Alpha model; no secrets in prompts) live in **`AGENTS.md` → "Freebuff Desktop risk notes"** — that section is the single source of truth and should be re-verified against the vendor tracker before trusting; this entry only cross-references it.
- **Mitigations in place this session:** both new files committed and pushed to origin; restore drill validated (tracked-file deletion → `git restore --source=origin/dev-dalton` → byte-identical in 0.040s); working tree clean.
- **Follow-up:**
  - [ ] Decide whether to send the server-side thread-history deletion request (draft scope in this conversation, 2026-09-28); re-check AGENTS.md against the vendor tracker first

---

## Auth rotation coverage — session-auth secret classes (C1B, LANDED Sept 30, 2026)

**Why this entry exists:** the platform's rotation/incident story previously covered
only three secret classes — the shared Neon `DATABASE_URL` password (#1), the armed
`API_KEY` (#5), and the local Postgres password (#3). Session auth
(`AUTH_MODE=session`, behind a feature flag) introduces new secret classes that did
not exist when the tracker was first written, so the rotation/incident picture was
incomplete. This entry closes that gap by recording what the new classes are and how
they are stored, sourced from `docs/SESSION_AUTH_PLAN.md` §4 (storage) and
`docs/API_SECURITY_REVIEW.md` (F5 login remediation).

**Secret classes now in scope:**

- **Password hashes** — `User.password_hash` in `db/models.py`. Stored encoded as
  `scrypt$n$r$p$salthex$hashhex` (stdlib `hashlib.scrypt`, n=2**14, r=8, p=1,
  16-byte salt, dklen=32); some Python 3.9 builds lack OpenSSL scrypt, so
  `verify_password` falls back to PBKDF2-HMAC-SHA256 at 600k iterations and
  dispatches on the stored scheme prefix. Compared with `hmac.compare_digest`.
  Plaintext password never stored; random salt means the same password hashes
  differently every time.
- **Session tokens** — `AuthSession.token_hash`. The cookie value is
  `secrets.token_urlsafe(32)`; the DB stores only its SHA-256 hex, never the raw
  cookie.
- **CSRF tokens** — `AuthSession.csrf_token`. A second per-session random; the
  client must send it back as `X-CSRF-Token`, compared with `hmac.compare_digest`.
- **The armed `API_KEY`** — unchanged from #5; in session mode a valid key still
  authenticates as an admin machine actor (it is a deployment credential), so it
  remains in the rotation story.

**Rotation / incident posture (summary):**

- Password rotation: change it via the user-CLI (`scripts/manage_users.py`)
  — `set-password` takes the new password from a `--password-env VARNAME` (an env
  var NAME, never the secret in argv or shell history) or a `getpass` prompt.
- Session revocation: logout deletes the `AuthSession` row; there is no
  password-change endpoint yet, so revocation-on-password-change is not wired today
  (tracked as a low-severity note in `docs/API_SECURITY_REVIEW.md`, F12).
- Expired sessions: `_session_from_request` checks expiry, but expired rows are
  never vacuumed automatically (same F12 note — minor DB growth, not an exposure).
- CSRF compromise: a stolen `csrf_token` is only useful together with the session
  cookie (double-submit), so it is tied to session lifetime; logout invalidates it.
- API key: unchanged — rotate in the BWS UI, re-pull, restart. In session mode the
  key is still accepted as admin (no CSRF needed for API-key actors).
- **Never-log rule:** logs and error bodies may contain usernames and actor kinds
  only — never passwords, tokens, cookie values, or CSRF tokens
  (`docs/SESSION_AUTH_PLAN.md` §6).

**What is NOT yet covered (carry into the tracker after C1B, as the GAP_AUDIT
intended):** a documented full-reset procedure for "session token or password hash
leaked" beyond the per-class notes above; expired-session vacuum; a
password-change/rotation endpoint if the team ever wants one. Those remain open
hygiene items, not missing gates.

**Sources:** `docs/SESSION_AUTH_PLAN.md` (plan + storage + risk controls); `docs/API_SECURITY_REVIEW.md` (F5 login remediation + F12 expired-session note); `api/auth.py` and `api/routes/auth.py` (live implementation).

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
- [x] **Done Sept 30 (local path):** server-injected `window.SOC_CONFIG` in `/index.modular.html` when the gate is armed (commit 99ee625). The Vercel deploy-time variant died with the retired deployment (Sept 30).
- [x] **Done Sept 30 (C4, commit `f7f6556`):** paste-box storage routed through boundary batches — `admit_text` + `record_paste_ingest` give every paste a manifest with `inserted_ids`; `purge_batch` undoes pastes; sanitization pipeline byte-identical (golden test)

---

## Admission hardening — C2.1.x request gates + C4 paste-batch (DEVELOPMENT_PLAN §11)

**Entries added 2026-10-05.** The gates themselves signed off Sept 30, 2026 (one commit per limit, per EXECUTION_PLAYBOOK "C2.1.x — one commit per limit"); recorded here with their regression tests so the admission semantics stay pinned. Shared decision helpers live in `api/helpers/admission.py` — `queue_rejection` (429), `payload_rejection` (413), `latch_rejection` (403), `concurrency_rejection` (409, per-resource occupancy vs. global saturation) — pure functions whose docstrings fix the boundary semantics: **inclusive** queue limit (a queue AT its limit is full), **exclusive** byte budget (the budget itself is the maximum), **fail-closed** mode latch (an unrecognized mode refuses).

### C2.1.2 — job-queue admission cap (429) ✅ LANDED
- **What:** `POST /api/execute` counts unfinished work across BOTH stores — the process-local `jobs` dict AND persisted `ToolRun` rows (`_unfinished_job_count()`) — and rejects with 429 **before** registry/disk work when `JOB_QUEUE_MAX` (env, default 100) is reached. A daemon restart (empty dict, persisted rows still unfinished) no longer silently resets the cap to zero.
- **Where:** `api/routes/tools.py:32` (constant + counter), `api/routes/tools.py:278` (admission check).
- **Regression tests:** `tests/test_api_key_gate.py::test_job_queue_cap_429` (full queue 429s before registry lookup — an unknown tool would 404; the cap must win; one freed slot admits again) · `tests/test_api_key_gate.py::test_job_queue_cap_429_counts_persisted_jobs` (post-restart state: empty in-memory dict + one unfinished persisted `ToolRun` still 429s; a completed persisted row frees the slot).

### C2.1.4 — paste payload cap (413) ✅ LANDED
- **What:** `POST /api/notables/paste` rejects raw payloads over `PASTE_MAX_BYTES` (env, default 5 MiB) with 413 **before any parsing/sanitization work**. Size is measured on the **encoded UTF-8 byte length**, never the character count — multibyte text can otherwise carry several times the budget past the gate.
- **Where:** `api/routes/notables.py:28`.
- **Regression tests:** `tests/test_api_key_gate.py::test_paste_payload_cap_413` (oversized paste 413s before parsing; an at-cap payload passes the gate and fails later at handler validation, proving ordering) · `tests/test_api_key_gate.py::test_paste_payload_cap_413_multibyte` (encoded-size boundary, not character count).

### C2.1.6 — tool subprocess environment (secret isolation) ✅ LANDED
- **What:** registered tool subprocesses run on an explicit env **allowlist** (`PATH`, `HOME`, `COMMANDER_BOOT`, plus anything named in `_TOOL_ENV_PASSTHROUGH`) instead of `os.environ.copy()`. The wholesale copy handed every registered tool the API process's secrets (`DATABASE_URL`, `API_KEY`, `OLLAMA_API_KEY`, `OLLAMA_URL`, ...). An audit of `Tools/**/*.py` found tools read exactly one env var (`COMMANDER_BOOT`, the interactive-prompt guard) and take everything else as CLI flags, so nothing legitimate is lost. The catalog-regression runner got the same allowlist — it spawns registered tools too, and would have kept leaking secrets through that path.
- **Where:** `api/routes/tools.py` (`_tool_env()` builder, used by **all three** `subprocess.run` sites in the file — `execute_tool_sync` (covers sync + async/background runs, since `execute_tool_async` funnels through it), the `/api/tools/regression` runner spawn, and the `/api/registry/reload` indexer spawn), `scripts/run_tool_catalog_regression.py` (`_tool_env()`, offline fixture runs). The regression-runner and indexer spawns inherit no env either: both scripts read zero environment variables (verified by audit), and the runner re-applies the same allowlist to the tools it spawns.
- **Regression test:** `tests/test_api_key_gate.py::test_tool_subprocess_env_is_minimal` (probe tool dumps its own env; the three API secrets are absent, the allowlist keys are present).

### C4 — paste-box storage through a boundary batch ✅ LANDED (commit `f7f6556`)
- **What:** every paste is admitted through the Splunk boundary (`admit_text`, the C4 paste-batch adapter) as the **first** pipeline action in `paste_notable`; `record_paste_ingest` writes that paste's `inserted_ids` onto the batch manifest exactly like `ingest_manifest` does for file batches, so `purge_batch` undoes any paste (DB rows + staged `{batch_id}-pasted.txt`). Sanitization output is proven byte-identical to the pre-C4 pipeline (golden test). Paste admission stays allowed in `quarantined` mode — the data enters the platform's own DB, not Splunk.
- **Where:** `services/splunk_boundary.py:198` (`admit_text`), `api/routes/notables.py` (`paste_notable` wiring ~`:1333`, failure-path purge ~`:1283`, manifest insert recording ~`:1570`).
- **Regression tests:** `tests/test_investigation_fixes.py::TestPasteBoundaryBatch` — `test_paste_creates_boundary_manifest` (paste → manifest with `source: paste-box`, `inserted_ids`, `ingest_window`) · `test_purge_batch_removes_pasted_rows` (purge deletes exactly the pasted ids + staged file) · `test_paste_sanitization_byte_identical` (golden: stored `sanitized_text` == pre-C4 pipeline output) · `test_paste_dedup_unchanged` · `test_paste_allowed_in_quarantined_mode`; plus `tests/test_api_key_gate.py::test_failed_paste_purges_boundary_batch` (a paste that fails after admission purges the staged batch — no orphaned copy of pasted content).
- **Cross-ref:** boundary checklist line under "Architecture hardening" above; `docs/RELEASE_NOTES.md` C4 entry.

---

## API security review — F1 + F5 remediations (2026-10-05)

Both fixes land with regression tests; `docs/API_SECURITY_REVIEW.md` is the source review.

### F1 — internal errors never leak exception text ✅ LANDED
- **What:** every 500 answers `{"detail": "Internal error"}`; the real exception is logged server-side (with traceback) under a request id. Previously `api/main.py`'s global handler returned `str(exc)` and ~40 route handlers did `raise HTTPException(500, detail=str(e))` — in API-key mode reads are ungated by contract, so anyone who could reach port 8000 could read SQL fragments, table names, host/port, or `OLLAMA_URL` from a 500 body.
- **Where:** new `api/helpers/errors.py` (`INTERNAL_ERROR_DETAIL`, `InternalError` (a 500 carrying the cause for the log), `raise_internal()`, `log_internal_error()`); `api/main.py` — request-id middleware (stamps `request.state.request_id`, echoes it in the `X-Request-Id` response header; a client-supplied id is honored) + both exception handlers (global handler logs + scrubs; the HTTPException handler scrubs **any** 500 as a safety net so a future `raise HTTPException(500, detail=str(e))` still cannot leak). Sub-500 details and non-500 5xx (the analysis 504 timeout) pass through untouched. Route sweep: `analyze`, `auth`, `closure`, `code_review`, `evidence`, `notables`, `promote`, `rules`, `splunk`, `tools`, `triage` all raise `InternalError`/`raise_internal(e, context=...)` instead of `detail=str(e)`.
- **Regression tests:** `tests/test_internal_errors.py` — global/HTTPException handler scrub + server-side log correlation (direct invocation), plain-500 safety net, sub-500/other-5xx passthrough, end-to-end unhandled/`InternalError`/plain-500 routes, a real swept route (notables paste) mid-handler DB failure, and the request-id header contract (generated, echoed, client-supplied honored).

### F5 — login: constant-time user resolution + throttling ✅ LANDED
- **What:** `POST /api/auth/login` used to short-circuit on a missing/inactive user, so a valid username cost a full scrypt/PBKDF2 run and an invalid one returned in microseconds — reliable username enumeration by timing; the endpoint also had no brute-force protection. Now every attempt verifies a password against a hash: missing/inactive users verify against a lazily built **dummy hash** (same KDF, same parameters), so every attempt pays exactly one KDF run. Failed attempts are counted on two independent sliding windows (per source IP and per attempted username, `LOGIN_MAX_FAILURES`/`LOGIN_WINDOW_SECONDS`, env, defaults 5/60 s); either window full → 429 **before** user resolution (even the correct password is refused while throttled). Success clears both windows.
- **Where:** `api/routes/auth.py` (`_dummy_password_hash`, `_login_windows`, `_login_throttled`, `_record_login_failure`, `_clear_login_failures`; the `login` handler reorder).
- **Regression tests:** `tests/test_api_key_gate.py` F5 block — spy-on-`verify_password` proofs (missing user → dummy hash; inactive user → dummy hash, never the stored hash; active user → real hash), a timing sanity test (missing-user login ≥ 40% of real-user login — both are one KDF run), throttle after repeat failures (incl. unknown usernames), independent per-IP/per-username windows, success clears windows, and window expiry.

---

## Checked and found OK ✅
- `.env` is gitignored (`.gitignore:6`)
- No `npg_` values or Neon hostnames appear in any tracked file
- Local Postgres password is not committed anywhere in git history
- `*.sql` / `*.db` / `*.sqlite` are gitignored
- CORS origins are an explicit allowlist, not `*`
- `allow_credentials=False` (reduces CSRF-style risk)
- Every push secret-scanned before it left the machine (pattern: `npg_*` + password/token/secret assignments)
- Boundary validation, latch modes, purge, and both ingest engines are unit-tested hermetically (sqlite-injected) — suite 121/121 on Python 3.14 and 3.9 at the time; 124/124 earlier Sept 28; **141/141 as of Sept 28, 2026** (Phase 2 verdict suite + confidence-hint tests landed)
- **pip-audit (Sept 28, 2026)**: `pip-audit 2.10.1` against `requirements.txt`, `requirements-dev.txt`, and the full installed 3.14 venv (includes transitive deps) — **no known vulnerabilities** in any pass (PyPI advisory DB, as of that date). Watch list per #11 unchanged (`fastapi`, `starlette`, `uvicorn`, `cryptography`, `sqlalchemy`) — re-run on dependency bumps.
- **Repo-wide credential sweep (Sept 28, 2026)**: pattern classes run across all 248 tracked files — `npg_`/Neon URLs, `sk-`/`ghp_`/`AKIA`-style keys, JWT/Bearer tokens, private-key blocks, `postgres://user:pass@` URLs, generic `(password|secret|token|api_key)=value` assignments, `.pem/.key/.db`-type tracked files. **Zero live findings.** `.env.example` still fully placeholder-ized (#8 fix holding); `deploy.yml` uses `${{ secrets.* }}` references only; `admin.jlee`/`WIN-APP-042` corpus confirmed synthetic per `CHAT_HANDOFF.md`. Caveats: (1) the 5 tracked `.pptx`/`.docx` binaries are not text-searchable — unaudited by this pass, low risk, spot-check once if desired; (2) dead credentials remain in git history by design (`theitguru` placeholder, rotated `remoteguest` password scrubbed from the working tree the same day)

## Open items, in order

> **Owner triage (Sept 28, 2026):** Dalton reviewed every people-blocked item live (Vercel string confirmation, auth activation, Dependabot toggle, `web/Old` deletion, Safari history pass, Freebuff deletion request) and called them **non-blocking — accepted for now**. Nothing below gates Phase 3; re-open individually if circumstances change. Largest accepted exposure: the unauthenticated prod API (items 3 above / #5–#6) — accepted knowingly.
1. ~~BWS access token / `bws run` launcher / slim `.env`~~ ✅ done (token live, `scripts/start` runs the API via `bws run`; `.env` is now token + non-secret config + keychain markers — #3 fix)
2. **New Neon URL distribution** → local side ✅ done (Sept 28 cutover); Vercel leg ✅ moot (Sept 30 — hosted deployment retired, project kept on ice per `docs/VERCEL_HANDOFF.md`)
3. **Auth activation decision** (coworker conversation) → `API_KEY` + server-injected `SOC_CONFIG.apiKey` locally (the Vercel leg died with the retired deployment, Sept 30)
4. **Dependabot alerts** (GitHub Settings → Security, ~2 min, you or Justin) — `pip-audit` already run clean (see "Checked and found OK")
5. **Deprecation cleanup** from the 3.14 warnings (`utcnow()` → timezone-aware; `httpx2`)
   - [x] **`utcnow()` — done Sept 28, 2026** (commits `e3b91fb` + `b816291`): shared `_utcnow()` helper (`now(timezone.utc)` stripped to naive — byte-identical output to the old calls, so the platform's naive-UTC storage convention is untouched) across `db/models.py` (11 Column defaults), `api/main.py`, `api/routes/system.py`, `services/closure_service.py`, `services/evidence_service.py`, and 6 seed/ingest/tool scripts. Suite: **124 passed, 1 warning** on Python 3.14 **and 124 passed on Python 3.9** (dual-runtime verified the same day — the helpers deliberately use `timezone.utc`, not the 3.11+ `datetime.UTC` shorthand, so both supported interpreters stay green). The only remaining warning is third-party (`anyio.BlockingPortal` via starlette's TestClient), not fixable here. SQLite roundtrip verified: model defaults still store naive UTC. One deliberate exception: `scripts/attic/add_code_review.py` keeps its utcnow — dead one-shot migration kept as historical record (its output already landed in `db/models.py`).
   - [ ] `httpx2`/TestClient preference — gated on starlette upstream; no action today
6. **Small decisions**: ~~`triage.db` keep/delete~~ ✅ archived per #12; Safari history cleanup for the old Neon URL (manual, TCC-blocked for tooling)
