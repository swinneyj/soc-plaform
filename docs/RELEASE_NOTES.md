# Release Notes — dev-dalton (pre-merge draft)

*Living draft — stage E.3 of `docs/EXECUTION_PLAYBOOK.md`. Finalized when
stage C lands; the `main` merge itself is human-executed (E.4).*

## What landed (by stage)

### Stage A — Security floor (P0) ✅
- **A1** `0e34724` — destructive actions now require the armed API key: GET
  delete wrappers removed, `GET /api/db/triage` delete side-effects stripped,
  frontend moved to POST delete, path-aware mutation gate, and the
  `TestRouteSemanticsAudit` route-allowlist lock (negative control proven).
- **A2** `4ba1391` + `6dd1fa3` — dependencies pinned and pruned (redis,
  python-pptx out; cryptography kept for the live `pki_cert_decoder` tool),
  dual-runtime `python_version` marker pins, pip-audit clean on the
  production branch, Dependabot config added.
- **A3** `dcc7551` — tracker + backend-plan statuses synced to reality.

### Stage B — Repo hygiene (P1) ✅
- `2288d2b` root one-shots → attic · `475b252` debris sweep, single README ·
  `c8b28ed` Tools staging copy → attic · `bd539b7` deployment-story matrix ·
  `638c73b` `.gitignore` BOM strip.

### Stage C — Product work (P2) ✅
- **C1** plan `7281fe8` (signed off Sept 30). **C1B.1** `30eae76` — users +
  sessions models, scrypt (+pbkdf2 fallback) helpers,
  `scripts/manage_users.py` CLI. **C1B.2** `4d4134e` — `/api/auth/*` routes,
  `resolve_actor` (session | api-key-as-admin | anonymous), session-mode read
  gate + CSRF middleware, `require_role("admin")` on the admin families.
  **C1B.3** `c795bdb` — login modal, header user chip/role badge/Logout,
  CSRF interceptor, one-place 401→login-modal hook, role-aware admin
  controls (tools execute/regression, jobs delete/clear, boundary purge).
  **C1B.4** `83f1dcd` — auth matrix tests (analyst 403, key-still-admin,
  expired session) + plan Status → BUILT. All inert until
  `AUTH_MODE=session`; flag-off suite green (byte-compat proven).
- **C2** `11778b5`+`786c23b`+`d75e57e`+`55f905a`+`933a375`+`3bc2fe6` — the
  five signed-off hardening limits, one commit each: analyze 300 s
  wall-clock (504 before persist), job-queue cap 100 (429), artifact 50 MiB
  (fail-closed via `splunk_boundary.validate_file` — `artifact_guard.py` has
  zero importers), paste 5 MiB (413), Ollama POSTs 30/min/IP (429). All
  env-tunable. *These are live on the next daemon restart (no flag).*
- **C3** `f52f572` — per-stage model selection: `AnalyzeRequest.stage_models`
  (initial|follow_up|closure), route resolution with base-model fallback,
  Stage 4/5 override selects in the wizard, `TestAnalyzeStageModels` ×3, new
  `analyze_stage_models` harness scenario (24 total). Fixed the pre-existing
  `res.data` ReferenceError at both ollama-metrics sites.
- **C4** `f7f6556` — paste-box storage through boundary batches:
  `admit_text()` + `record_paste_ingest()` in the boundary service,
  `paste_notable` admits first then runs the pipeline byte-for-byte and
  returns `batch_id`; `purge_batch` now undoes pastes. Sanitization golden +
  manifest/purge parity + dedup + quarantined-mode tests (5).

### Stage D — Externals & wait-states (P3) ✅ (docs assembled)
- **D1** `3d614cc` — [Vercel handoff](VERCEL_HANDOFF.md): env vars, routing
  question, deploy pipeline, break table, owner checklist.
- **D2** `4032b23` — [Splunk rehearsal](SPLUNK_REHEARSAL.md): five-step
  live-connector checklist, credential-delivery rule included.
- **D3** — user-side hygiene folded into the handoff doc §6.

## Final gate counts (E.1 sweep at `f7f6556`, clean tree)
pytest **304 × 2** (3.14 + 3.9), undefined-names OK, merge-markers OK,
baseline holding, `node --check` clean (all web JS), **24** UI harness
scenarios green, `delete_case_id` grep = 0, test-file count = 8,
quarantine staging clean.

## Known-open items (recorded, not blocking)
- **`AUTH_MODE` flip deliberately deferred** (decision Sept 30: flag-off
  posture ships) — see the Stage E checklist note.
- **Vercel env vars + routing answer** (Justin) — `docs/VERCEL_HANDOFF.md` §5.
- **Dependabot vulnerability-alerts toggle** — needs repo admin
  (Settings → Code security); `dependabot.yml` itself is live.
- **3.9 accepted-risk pins** — starlette 0.49.3 / pytest 8.4.2 have advisories
  with no 3.9-compatible fix; retires with the 3.9 CI leg (tracker #9).
- **Live Splunk rehearsal** — blocked on `SPLUNK_URL`/`SPLUNK_TOKEN`
  (`docs/SPLUNK_REHEARSAL.md`).
- **D3 user-side hygiene** — Safari scrub, thread-deletion decision.

## Ops entry points
- `scripts/start` — one-command start; rides backup (20 h staleness skip) +
  retention dry-run on every start; `scripts/start --backup` forces a dump.
- Secrets: BWS vault + macOS Keychain (`scripts/secrets-keychain`); `.env`
  regenerated via `scripts/pull-secrets`. Never in Git, never in chat.
- Deploy: push to a deploy branch → `deploy.yml` → prod (last push wins);
  smoke = `GET /api/health` → `{"status": "healthy", …}`.

## Stage E remaining checklist
- [x] E.1 final sweep green on a clean tree at `f7f6556` (counts above)
- [x] E.2 status sync — tracker paste-batch checkbox closed, dev-plan §11
  Phase C marked complete
- [x] E.3 release notes completed (this document)
- [x] **Auth posture decision (Sept 30): ship flag-off.** The runtime stays
  API-key-gated exactly as today; session auth (C1B) remains dormant but
  CI-tested. Flipping later needs no release: create a user via
  `scripts/manage_users.py`, set `AUTH_MODE=session` (+ `SESSION_SECURE=1`
  behind HTTPS), restart per `scripts/start` — rollback is unsetting the
  var (`docs/SESSION_AUTH_PLAN.md` §6).
- [x] **E.4 daemon restarted** Sept 30 — running release code (`main @
  e6aff09`); live smoke passed: seed cases, draft round-trip, paste →
  boundary batch → purge, 413 paste cap, real Stage 3+4 analyses via
  `stage_models`, 429 rate-limit probe, health unaffected.
- [x] **E.4 CI green + clean tree** at the release commits (`main e6aff09`,
  `dev-dalton 1919285`)
- [x] **E.4 merge executed** — `main` fast-forwarded to `origin/main`
  (autosave-drafts feature) then `--no-ff` merge of `dev-dalton`
  (`eeefe06`); the drafts feature was ported into the modular routers
  (`c41e7c8`) with a round-trip test
- [x] **E.4 tagged `v1.0.0`** (annotation = this document's summary)
- [ ] **E.4 deploy** per `docs/VERCEL_HANDOFF.md` — blocked on env vars +
  routing answer (Justin); smoke `GET /api/health` + one authenticated UI
  write once deployed
