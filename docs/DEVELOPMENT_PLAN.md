# SOC Platform — Development Plan

*Last updated: 2026-09-29. Owner: Dalton Lewis · Repo: `swinneyj/soc-plaform`*

---

## 1. Where we are (snapshot)

- **Code state:** `dev-dalton` at `8ab1044` — Phase 1 hardening ✅, Phase 2 per-card AI evidence verdicts ✅ (incl. confidence-hint polish), and Phase 3 read-only Splunk integration ✅ pushed (mock loop + `RealSplunkBackend` REST connector, the mid-loop search-one harness, Run All + inline run status, and the Sept 29 live-smoke fixes in §8); CI green on Python 3.14 + 3.9.
- **Data state:** one shared **Neon** dataset — local API, Vercel prod, and Justin's instance all read/write the same database. Test rows must be clearly marked and deleted the same session; nothing destructive without explicit confirmation.
- **Runtime:** one-command startup via `scripts/start` (Ollama + BWS preflight + daemonized API on `127.0.0.1:8000`), Ollama `llama3.1:latest`, Neon Postgres (cloud) — app ports loopback-only. Secrets: BWS vault is source of truth (`scripts/pull-secrets` regenerates `.env`); credential-bearing keys live in the **macOS Keychain** (`scripts/secrets-keychain`), not in plaintext `.env`.
- **Test suite:** 276 tests (pure-unit + TestClient), <2 s runtime, dual-runtime (3.14 + 3.9) matrix in CI, covering evidence ledger validation, AI-derived direction scoring, per-card verdict parsing/precedence, question-driven follow-ups (incl. targeted-evidence question resolution), the Phase 4 advisory-label contract (analyst labels and the stored pre-loop verdict never decide outcomes; promote pins verdict to "suspicious" at the 0.80 gate baseline), model-tag resolution, closure gating, confidence caps/hints, loop-status transitions, boundary/latch, report paths, the notable-parsing pipeline (`evidence_service`), the mid-loop search-one loop harness (phases 2–5 with real executions), the playbook family guard, `RealSplunkBackend` REST contract tests (hermetic via an injectable session), Phase 5 retention selection + API-key activation, the Phase 4 judgment-migration sweep, a pyflakes undefined-name gate in CI (`scripts/check_undefined_names.sh` — 3.14's lazy annotations mask missing typing imports locally, so the F821 class is gated explicitly), and UI regressions via zero-dependency node vm harnesses (`tests/ui_regression/`, shared `harness_core.mjs`) driving the real `runSplunkSearch` plus the evidence save / promote / saved-evidence-load flows (the load path is regression-locked against the `6942458` crash — hydration assertions plus the `console.error` canary fail if legacy raw_result rows ever stop loading; canaries also assert zero blocking `alert()` calls and no swallowed crashes). S7 (Sept 29): per-card analysis state is unified under one `phase2CardState` map — `{ resultText, findingType, editedSpl, coverage, status, runStatus }` keyed by the existing query keys — replacing six parallel keyings; the harness fixtures drive the same shape, and the collection-status select now saves from root state (it was previously read from a child-local map the save methods could never see).
- **Known debt:** ops backlog remainder (Phase 5: session auth/roles, service hardening, multi-model per-stage choice; backups + retention landed Sept 29, activated Sept 30 via start-riding ops instead of launchd — no schedulers by decision — and the API-key UI wiring is server-injected, both verified live Sept 30), Vercel env-var handoff pending Justin, and dead monolith remnants pending cleanup (Sept 29: the corrupted legacy tail in `index.html` was stripped, the 9 zero-reference root one-shots moved to `scripts/attic/`, `api/main.py` bare `print()` error paths converted to structured `logging` — last-resort handler keeps stderr so log redirects are unaffected — and `api/main.py` split into focused routers + `api/schemas.py` + `api/helpers/*` (`api/main.py` is now a ~155-line app shell, see `docs/BACKEND_MODULARIZATION_PLAN.md`) with dead `api/db_routes.py` retired to `scripts/attic/`; and `web/app.js` + `web/Old/` retired to `scripts/attic/` — they were referenced by nothing and every fix risked drifting into the dead copies; Sept 30: `web/Old_archive/` relocated to `scripts/attic/web-Old_archive/`, leaving the served web/ tree live-only). Phase 4 closed Sept 29: the last analyst-judgment inputs (promote-time triage verdicts + `question_resolution`) are advisory-only.

### Collaboration hazard (read this first)
Justin has twice replaced repo history with fresh single-commit snapshots ("updated project files from WIndows"). Any local line not pushed is at risk of being orphaned. **Rule: push early, push often; re-verify his snapshots against the test suite before adopting them** (`pytest` catches regressions in <1 s).

---

## 2. Immediate actions

*Owner triage Sept 28, 2026: all deferred as non-blocking (see tracker "Open items, in order") — Phase 3 can start now.*

1. **Vercel env vars (Justin):** new Neon `DATABASE_URL` + `API_KEY` — the classic miss that breaks the next deploy; deploy.yml's `/api/health` smoke test catches it loudly.
2. **API-gate activation decision (Dalton + Justin, ~10 min):** `API_KEY` is in the vault and the frontend already sends `X-API-Key` (`web/utils/auth.js`); since Sept 29 the gate covers every mutating `/api/*` route (not just the 7 dangerous ones). Setting `SOC_CONFIG.apiKey` in the deployed HTML flips it on (unlocks `restricted` Splunk-boundary mode too).
3. **Dependabot alerts toggle** (GitHub Settings → Security, ~2 min).
4. **Then Phase 3** (read-only Splunk REST) — see §5; the mock `SearchBackend` means zero Splunk credentials needed to start.

---

## 3. Phase 1 — Hardening (small, high-value) ✅ COMPLETE (Sept 28, 2026)

| Item | Status |
|---|---|
| CI workflow | ✅ `.github/workflows/tests.yml` — 124-test suite on every push/PR, Python 3.14 + 3.9 matrix; first runs green (run 36437812481 + successors) |
| API-level tests | ✅ pre-existing — `tests/test_api_analyze_flow.py` + key-gate + report-download suites cover the TestClient surface (124 total) |
| Doc alignment | ✅ `20e5dba` — README/SOP document the auto-resolution order + `OLLAMA_MODEL` pinning; SETUP.md already correct; no stale 8b requirements |
| Helper de-dup | ✅ `ff07707` — service copy is the strict superset; api/main.py keeps thin aliases; net −84 LOC |
| Stray-file guardrail | ✅ `*.bak`/Copy patterns pre-existing; `.pytest_cache/` added `be2143f`. `web/Old/**` retired to `scripts/attic/web-old/` Sept 29 |

**Exit criteria:** CI green on GitHub; no duplicated prompt/grounding logic; docs match runtime behavior.

---

## 4. Phase 2 — Per-card AI evidence verdicts

Complete the "analyst brings logs, AI decides" principle at the per-entry level.

- **Design:** extend the analysis prompt to return a `PHASE2_EVIDENCE_JSON` block: `[{title, direction: supports|refutes|neutral, rationale, confidence_delta_hint}]` for each evidence card it reviewed.
- **Storage:** new nullable columns/JSON on `SupportiveQueryResult.raw_result` (`ai_direction`, `ai_rationale`, `ai_assessed_at`) — legacy rows stay valid with nulls.
- **Scoring:** `_infer_direction_from_analysis` becomes the fallback; per-card `ai_direction` takes precedence when present. *(completed Sept 28: rebuilds now honor the persisted `ai_finding_type`/`ai_verdict_rationale` — see §8.)* Aggregate rationale feeds the Investigative Analysis section.
- **UI:** evidence timeline shows the AI's per-card verdict chip with rationale tooltip; analyst still enters nothing but execution facts.
- **Tests:** prompt contract (JSON block present), parser (malformed → fallback), scoring precedence.
- **Polish (done):** `confidence_delta_hint` nudges disposition confidence ±0.02 per structured verdict, capped at ±0.06 aggregate, applied before disposition caps and the final clamp (hints refine but never outweigh the ledger or breach guardrails). Serializer exposes `confidence_hints` counts; UI shows a distinct violet **EVIDENCE JSON** chip (vs indigo **AI ASSESSMENT**) plus the hint on evidence cards.

**Size:** M–L. **Depends on:** nothing; can start after Phase 1 helper de-dup (touches the same code).

---

## 5. Phase 3 — Read-only Splunk integration (mock-first delivered)

Turn the paste-driven loop into a one-click loop. **Read-only first** (search + results fetch; no writes to Splunk).

**Status (Sept 29, 2026): delivered — mock loop + real read-only connector.** Everything runs end-to-end against the deterministic `MockSplunkBackend` (default), and `RealSplunkBackend` (Sept 29) executes SPL over Splunk REST — single round-trip `POST /services/search/jobs/export`, bearer auth, CSV rows normalized to the mock contract shape (epoch `_time` → naive-UTC ISO `timestamp`) — activated by `SEARCH_BACKEND=splunk` + `SPLUNK_URL`/`SPLUNK_TOKEN`. Unconfigured construction fails loudly (`SplunkSearchError`) instead of silently pretending; untested-against-a-live-Splunk is the only remaining gap.

- **Flow (shipped):** `POST /api/splunk/search-one {case_id, query_title, spl?, earliest?, latest?, target_questions?}` → substitute placeholders (`$host$`, `$user$`…) from the case's notable fields via `rule_context_service.render_query_template` (DB-backed `PlaceholderAlias` overrides + built-in defaults; unresolved tokens ⇒ 422, never a silently broken query) → run through `get_search_backend()` → map job outcome to `result_status` (`map_search_outcome`: 0 rows ⇒ `no_results`; error/timeout ⇒ `query_failed`; else `success`) → save through the standard evidence-save path with the explicit `splunk_auto` label (re-runs replace the prior auto row per query title) → return bounded raw rows for analyst review.
- **Guardrails (shipped):** per-case concurrency cap (1 running job; second run ⇒ 409), 60 s timeout (`SEARCH_ONE_TIMEOUT_SECONDS`), every executed query text logged to the `tool_runs` audit trail (`tool_name=splunk_search_one`).
- **UI (shipped):** "Run in Splunk" button on Stage-2 supportive query cards and Phase 2 follow-up cards; results land in the card notes and the Evidence Timeline ledger immediately (investigation state rebuilt on save). Sept 29: **Run All** on Stage 2 (sequential, skips busy cards, summary banner) and non-blocking inline per-card run status (`running → Saved ✓ / Run failed`) replacing modal alerts on every run path. S8 (Sept 29): bulk promote gives per-item outcome lines (promoted ✓ / failure + detail), a promoted/failed/skipped summary, and live progress, and explains the no-candidate no-op instead of staying silent; the disabled historical button carries a tooltip reason (closed record, or already promoted as `<case>`). S9 (Sept 29): the Closure tab shows a readiness punch list (`GET /api/db/triage/{case}/closure-readiness` now annotates each blocker with a `{stage, label}` routing action); every blocker deep-links to the analysis stage that resolves it (evidence/state → stage 1, failed queries/data gaps → stage 2, assessment work → stage 4), a blocked generate surfaces the same punch list, and the harness `confirm` stub covers the decline/force paths. S10 (Sept 29): analyst card edits auto-save as debounced draft snapshots (~1.2s trailing edge) via root setters (`_updateCardField`/`updateSupportiveResult`), so a refresh or crash loses at most the last 1.2s of typing; switching cases cancels the pending write. S11 (Sept 29): the Database tab gains a case-level evidence ledger over the durable `supportive_query_results` rows — pick a case, browse every saved evidence item with source/id/timestamp and a result preview, delete individual items via the same batch-delete route. S12 (Sept 29): the Stage-5 dashboard gains a loop timeline panel — a chronological evidence rail (oldest first) with per-item direction coloring (supports/refutes/neutral), confidence delta hints, and AI rationale, derived defensively from the persisted timeline via `buildLoopTimeline`. S13 (Sept 29): canonical REST resource paths (`/api/cases`, `/api/notables`, `/api/evidence/{case}`, `/api/analyses`) alias the historical `/api/db/*` routes via an ASGI rewrite in `api/main.py` — handlers untouched, legacy spellings keep working, alias/legacy parity unit-tested. Post-S13 fold (Sept 29): the last parallel keyings (`supportiveManualResults`/`supportiveFindingTypes`/`enrichmentManualResults`/`enrichmentFindingTypes`) are gone — every per-card field (supportive, enrichment, phase2) lives in `phase2CardState` under one keying scheme, and old localStorage snapshots migrate into card cells on load (legacy-map folding survives mixed snapshots; the S7 note's harness fixtures drive the folded shape). S13 client adoption (Sept 29): the frontend's service layer (`web/modules/api.js`) now upgrades every aliased route to the canonical spelling before dialing and retries the legacy `/api/db/*` path once on canonical 404 (older deploys); non-404 errors and non-aliased routes never fall back. Domain modules still call axios directly and can adopt the layer call-by-call.
- **Auth/config (delivered with the connector):** `SPLUNK_URL`, `SPLUNK_TOKEN` (bearer) read from env at construction (BWS/keychain injection, never in DB); the connector is read-only by design — no job management, no writes. Session-key flow rejected as unnecessary complexity.
- **Non-goals (v1):** saved-search management, index writes, ES notable updates, multi-cluster.
- **Tests:** placeholder substitution, outcome mapping, and row summarization (pure, `tests/test_search_backend.py`); `RealSplunkBackend` contract (same file) — export request shape (URL/bearer/timeout/payload), `search ` prefixing vs generating commands, CSV normalization incl. epoch→ISO, stats-row shape defaults, HTTP-error and transport-error wrapping, `execute_for_case` payload, factory selection — all hermetic via a duck-typed injectable session; endpoint via TestClient — substitution, `no_results`/`query_failed` mapping, per-title replace, 404/422 guards, concurrency 409, timeout (`tests/test_api_analyze_flow.py`); mid-loop harness `TestSearchOneMidLoop` (phases 2–5 interleaved with real search-one executions — exactly one `splunk_auto` row per (case, query), auto-evidence consumed by the next phase's prompt) and embedded `earliest=`/`latest=` window parsing (5-tuple `_parse_spl`; embedded windows override the caller's).

**Size:** L. **Depends on:** Phase 1 API tests (so the ledger path is pinned before automating it). **Remaining:** live rehearsal against a real Splunk instance once the API key arrives (set `SPLUNK_URL`/`SPLUNK_TOKEN`, flip `SEARCH_BACKEND=splunk`).

---

## 6. Phase 4 — Judgment-flow audit (same principle, other surfaces)

Sweep remaining analyst-judgment surfaces with the evidence-model lens:

- Closure-note generation: **done (Sep 28, 2026)** — the disposition is now derived server-side from the evidence-backed `investigation_state.provisional_disposition` (`closure_service.derive_closure_disposition`); the caller's value is advisory/audited only (`operator_disposition` + `disposition_conflict` in the response) and the UI disposition select is disabled. Rule-required `field_values` render only under an attributed "Operator-Recorded Closure Fields (execution facts, not conclusions)" header and can never reach the conclusion sentence — pinned by tests in `test_closure_gate.py` / `test_api_analyze_flow.py`.
- Triage verdict entry on promote: **done (Sep 29, 2026)** — the pasted ES disposition is the *referring* analyst's conclusion; it stays on the notable record for context but is never promoted into the case. Promoted verdicts are fixed to `suspicious` (an alert is, by definition, unverified) and confidence starts exactly at the 0.80 closure gate (`derive_triage_confidence`), so the evidence ledger alone moves the number — earned evidence closes naturally, refuting/missing evidence stays gated. `derive_triage_verdict` (the disposition→verdict mapper) was deleted, and the loop no longer reads the case row's stored verdict as a disposition fallback (the `benign_tracked` upgrade guard went with it — ledger evidence upgrades any case).
- Inquiry resolution remnants in Phase 2 state (analyst `question_resolution` values still stored): **done (Sep 29, 2026)** — the label is advisory and audited only. Resolution is derived from the evidence itself (substantive rows executed against `target_questions`, Sept 29) and the model re-raises anything it still doubts in the next phase's Key Questions. The modular UI's dead resolution plumbing (`phase2ResolutionTypes`/`phase2ResolutionQuestions` — snapshot/props/events with no rendered control) was removed; legacy rows carrying resolution labels keep round-tripping through the timeline. (The removal left stale rehydration writes in `loadSavedPhase2Evidence` that crashed every saved-evidence load — fixed in `6942458`, now mutation-locked by the `load_saved_evidence` harness scenario.)

**Size:** S–M each. **Exit criteria:** the only analyst inputs anywhere are execution facts and observations — **met Sept 29, 2026**. The one remaining analyst-labeled field on evidence (`finding_type`) was already advisory via the AI-direction work; disposition at closure is server-derived (above).
- Legacy data migration: **done (Sep 29, 2026)** — `scripts/normalize_phase4_judgments.py` (dry-run default, `--apply`, JSON archive of every old value in `local-backups/`) normalized the shared Neon DB to the contract: triage verdicts/confidence → `("suspicious", 0.80)`, evidence `question_resolution` labels → `"not_resolved"`, and `resolved_questions` keeps only evidence-backed resolutions (pure logic in `services/judgment_normalization.py`, substance rule mirrors the state builder). Sweep result: 10 demo-scenario rows normalized (old judgment scores 0.61–0.97 in the baseline field), zero label/state contamination; idempotent. Fixed Sep 29: both seeders now write the contract constants directly (the per-scenario verdict/confidence dicts are gone), so re-seeding can no longer reintroduce old semantics — mutation-checked by `TestSeedContractConformance`, which asserts every seeded row is invisible to the sweep.

---

## 7. Phase 5 — Operations & production readiness

- **Auth (groundwork done Sep 29, 2026):** every mutating `/api/*` request now requires the API key when `API_KEY` is set — a method-based middleware gate covers all current and future routes (the 7 dangerous routes keep their explicit `require_api_key` dependency as defense in depth); reads, `/api/health` (deploy smoke), and the static UI stay open by contract. The frontend already stamps `X-API-Key` from `window.SOC_CONFIG.apiKey` (`web/utils/auth.js`), so **activation is three steps**: put `API_KEY` in the BWS vault + `.env`, set `SOC_CONFIG.apiKey` in the deployed HTML, restart. Pinned by `TestApiKeyActivation` (header/query key accepted, wrong/missing key 401, reads open). *Remaining before non-localhost exposure: session auth + role split (analyst vs admin).*
- **Backups (done Sep 29, 2026; activation reworked Sept 30):** `scripts/backup_db.sh` (gzipped plain-SQL `pg_dump` into gitignored `local-backups/`, `BACKUP_KEEP`-count retention, SQLAlchemy driver-suffix normalization, atomic temp-file writes so failed dumps leave nothing behind, prefers the libpq keg's `pg_dump` — Neon runs PG 18, so the Homebrew PG 16 client refuses) + restore runbook in `docs/BACKUP_AND_RESTORE.md`. The launchd schedule was deliberately *not* installed (user decision): ops activation now rides along with `scripts/start` — every platform start refreshes the backup (skipped when the newest dump is < 20 h old) and `scripts/start --backup` forces one on demand; verified live against Neon with a fresh gzip-intact dump plus the staleness skip on the next start.
- **Service hardening (open):** review launchd plists (restart throttling, log rotation for `soc-api.log` / Ollama logs).
- **Multi-model (open):** model selector already exists in UI; expose per-stage model choice (analysis vs closure) and record it per iteration (DB default now `llama3.1:latest`).
- **Data retention (done Sep 29, 2026; activation reworked Sept 30):** `services/retention_service.py` — keep the last N `analysis_results` per case (default 20) with closure-linked cases never pruned (a case with any closure note is permanent record); dry-run by default via `scripts/prune_analysis_results.py --apply` to delete. Pure selection logic + DB wrapper, mutation-checked. Per the no-scheduler decision, every `scripts/start` writes a fresh dry-run report (`/tmp/soc-retention.log`); apply-time pruning stays a deliberate manual step (`RETENTION_APPLY=1 … --apply`), never automated.

---

## 8. Known issues

- **SPL follow-up phase degradation (reported Sept 28, 2026 — RESOLVED Sept 28):** after Phase 3 the iterative investigation stopped surfacing new queries and the loop view degraded ("basically stopped working overall"). Diagnosed with the loop-stress harness (`tests/test_api_analyze_flow.py::TestFollowUpLoopStress`, which auto-drives 7 follow-up iterations against a 2-template playbook and asserts every phase returns cards carrying a *fresh* SPL; extended Sept 28 with `TestSearchOneMidLoop`, which interleaves real `POST /api/splunk/search-one` executions between phases and asserts the auto-collected `splunk_auto` evidence executes successfully against the mock backend, ledgers exactly once per (case, query), and is consumed by the next phase's prompt — covering the full analyst loop: propose → execute → consume). Root causes and fixes: (1) re-check variants cloned the template SPL verbatim — same query under new phase-stamped titles = "no new queries" → variants now re-scope the time window (`earliest=-<phase>h`, `_rescope_variant_spl`); (2) the Phase 3+ builder dead-ended once targets emptied while the loop still needed work → it now falls back to hypothesis-verification re-checks whenever `loop_status != ready_for_closure` (empty cards only when the loop has converged); (3) questions raised by the current analysis were not targets until the next iteration → `current_questions` merge; (4) `ai_finding_type` per-card verdicts persisted by `/db/analyze` were written but never read back, so post-save state rebuilds degraded every entry to neutral ("evidence marked neutral only") → rebuilds now honor persisted per-card verdicts. Known nuance: re-saving evidence replaces rows and clears stored verdicts until the next analysis re-assesses the new results (by design).

- **Modular analysis-tab crash + blocking run alerts (reported Sept 29 from the live UI smoke — RESOLVED Sept 29):** the modular tab died blank when phase-2 cards rendered (`AnalysisTab` read `phase2CoverageNotes` from a prop `index.modular.html` never passed, plus an undeclared `supportiveSaveBusy`), and `runSplunkSearch`'s success `alert()` can wedge embedded webviews mid-loop. Fixed: root state + bindings + `?v=` cache-buster bumps; inline per-card run status (`running → Saved ✓ / Run failed`) replaces the modal on every path with Run disabled while running; locked by a zero-dependency node vm harness (`tests/ui_regression/run_splunk_search_status.mjs`) that drives the real `runSplunkSearch` and asserts chip transitions and zero `alert()` calls across 5 scenarios.

- **Stepper pre-completion with no case selected (reported Sept 30 from the S7–S13 live UI smoke — RESOLVED Sept 30):** with no case chosen, `AnalysisTab.stage2Complete`'s "no playbook" fallback marked Evidence Collection ✓ as soon as the page loaded, and `scripts/start --check` started the API daemon as a side effect (plain-`nohup` Ollama also died with the launching shell). Fixed: `stage2Complete` now requires an active case (unsupported-rule fallback preserved for real cases); Ollama daemonizes via the same double-fork/setsid pattern as the API and `--check` is strictly read-only status. Locked by `tests/ui_regression/evidence_promote_load.mjs::stepper_guards`, which drives the real computeds and fails against the pre-fix component (verified by negative control).

- **UI could not authenticate against an armed API-key gate (found Sept 30, RESOLVED Sept 30):** the API daemon enforces `API_KEY` (BWS-injected — mutating requests 401), but `index.modular.html` shipped no `window.SOC_CONFIG` bootstrap, so the browser never sent `X-API-Key` and every UI write failed with `Invalid or missing API key`. Fixed (server-injected approach, user-selected): `/index.modular.html` is served through a small route that embeds `window.SOC_CONFIG = {"apiKey": …}` before the first script tag when the gate is armed, verbatim otherwise (never stored in the repo). Verified end-to-end live: Run All → both mock-Splunk queries auto-ran and saved through the UI, Save & Continue appended ledger rows, and the wizard advanced to Stage 3 — all against the armed gate. Locked by three `TestApiKeyActivation` tests (injection presence + placement, verbatim-when-disarmed, injected-key-authenticates). Note: anyone who can load the UI can read the injected key — inherent to browser-side auth, acceptable while the UI is localhost/Tailscale-only.

- **Playbook family hijack (reported Sept 29 from the live UI smoke — RESOLVED Sept 29):** `/api/db/analyze`'s fuzzy `supportive_rules.json` matcher adopted an unrelated catalog entry (MOCK-RULE-001 matched `sso_brute_force_pingfederate` on shared anchor tokens) even for rules that own DB-backed `SupportiveQuery` rows, zeroing `supportive_playbook_available` and hiding the Stage 4 follow-up generator mid-investigation. Fixed: fuzzy catalog adoption now only fires when the case's rule has no DB-backed queries (`api/main.py`); regression tests `TestPlaybookFamilyGuard` include a counter-test that playbook-less imported notables still adopt catalog families.

---

## 9. Verification strategy (applies to all phases)

1. Pure-unit tests for any new logic (no DB/Ollama) — keep the suite under ~1 s.
2. Contract tests for prompts and external payload shapes.
3. Live rehearsal before each merge to `main`: paste → promote → evidence (incl. empty no-results) → analyze → follow-up phase → closure gate.
4. `pytest` immediately after adopting any upstream snapshot from Justin.

---

## 10. Suggested sequence (historical — all steps complete)

```
Push queued commits → Phase 1 (CI + API tests + docs) → Phase 2 (per-card verdicts)
→ Phase 3 (Splunk read-only) → Phase 4 (judgment audit) → Phase 5 (ops)
```

Phases 2 and 3 are independent of each other after Phase 1; pick by appetite — Phase 2 deepens the analysis model, Phase 3 removes the manual paste bottleneck.

---

## 11. Execution roadmap (Sept 30, 2026 — full-codebase review)

**→ Mechanical step-by-step runbook for executors: [`docs/EXECUTION_PLAYBOOK.md`](EXECUTION_PLAYBOOK.md)**
(every item below is expanded there into atomic steps with exact edits, verify lines,
guardrails, and commit templates).

**Health snapshot:** 75+ endpoints across 11 routers (`api/routes/`), 12 services, 11 ORM
models; 8.9k-LOC modular frontend, zero axios outside the transport; 276 tests + 23
node-vm harness scenarios, green on 3.14 + 3.9; zero TODO/FIXME/HACK debt in live
code. The foundation is solid — what remains is security floor, hygiene, the named-
but-unplanned Phase 5 remainder, and externals. Phased by risk:

### Phase A — Security floor (P0 · one session)

- **A1 — Close the GET-delete mutation-gate bypass.** `/api/db/notables/{event_id}/delete`
  and `/api/db/triage/{case_id}/delete` are deliberate GET wrappers ("for environments
  that disallow POST/DELETE") that call the destructive handlers directly — when
  `API_KEY` is armed they sail past the method-based mutation gate (`POST/PUT/PATCH/DELETE`
  only), leaving two unauthenticated destructive endpoints. The UI already uses the
  POST spellings (`API.deleteNotable`, `deleteTriageCase`), so: **remove the GET wrappers**
  *and* make `mutation_gate_rejects` path-aware (`*/delete`, `*/batch-delete`,
  `*/delete-all` count as mutations regardless of verb) — belt and suspenders. Update
  `tests/api_path_contract.json` spellings + `docs/API_ENDPOINTS.md` (the V2 doc-drift
  guard enforces accuracy). **Exit test: a route-semantics audit** — enumerate
  `api_main.app.routes` and assert every GET route sits in an explicit read-only
  allowlist; any future GET route fails CI until classified. Closes the whole bug class.
- **A2 — Supply chain.** `requirements*.txt` are completely unpinned (zero `==`); three
  deps have zero live imports (`redis`, `python-pptx`, `cryptography` — verify psycopg
  needs none of them, then drop). Pin floors, run `pip-audit`, enable Dependabot alerts
  (tracker items). Exit: clean audit + reproducible installs.
- **A3 — Doc-status sync.** The tracker's “Wire `SOC_CONFIG.apiKey` into the served
  pages” item is done (server injection, `99ee625`); `BACKEND_MODULARIZATION_PLAN.md`'s
  “further phases planned” header is stale (Pieces A–D landed). Refresh both and check
  off the tracker's API-key-dependency item (middleware gate covers it).

### Phase B — Repo hygiene (P1 · one session)

- **B1 — Root triage.** Zero-reference one-shots → `scripts/attic/`: `inspect_db.py`,
  `ingest_notables.py`, `wipe_db.py`, `seed_dummy_closed_notables.py`,
  `close_freebuff_tabs.py`, `add_code_review.py`. **Keep** `commander.py` (live via
  `Tools/automated_reporter`) and seeders with refs (`seed_test_cases.py`,
  `seed_supportive_results.py`). **Open decision:** the Windows ops set (`*.ps1`,
  `*.bat`, `Reset-DummyDb.ps1`, `git-flow*`) — is Windows still Justin's deployment
  story? Hold until D1 answers, or attic now.
- **B2 — Debris.** 4 PPTX + 2 DOCX (~3.8 MB): keep one FINAL deck, attic the rest.
  `sample_rules.json` vs `updated_rules.json` are byte-size twins — hash-compare, drop
  one. `README.txt` vs `README.md` — one README wins. `Downloads_Map.txt`,
  `README_MOVED_*.txt`, `BUILD_SUMMARY.md`, `multi-user-workflow.md`, `payload.json`,
  `unsupported_rule_splunk_results.csv`, `closure_form_snippet.html`, `code_review_ui.html`,
  `*.code-workspace` ×2 → attic or delete per age.
- **B3 —** `Tools/chat_language_models_2_-_staging_copy/` → attic (dead copy inside the
  live tool catalog).
- **B4 — Deployment-story matrix.** Three coexisting stories: Mac `scripts/start`
  (canonical, start-riding ops), `docker-compose` (Linux/Windows), Vercel (Justin's
  target). Add a support matrix to `SETUP.md`/`CONTAINERIZATION.md`. Note: there is no
  `vercel.json` — routing (does `/index.modular.html` hit the FastAPI app or Vercel's
  CDN?) lives in dashboard config and determines whether the SOC_CONFIG injection
  works on Vercel. Capture it during D1.
- **B5 —** `.gitignore` opens with a UTF-8 BOM — strip.

### Phase C — Phase 5 product work (P2 · several sessions · plan-then-build)

- **C1 — Session auth + analyst/admin roles.** Prerequisite for any non-localhost
  exposure (tracker gates public exposure on it). Write `docs/SESSION_AUTH_PLAN.md`
  first in the modularization-plan style: session cookie + CSRF for the UI, keep
  `X-API-Key` for API clients, roles gate admin surfaces (tools/registry, jobs,
  boundary admit/release, retention apply).
- **C2 — Service hardening.** Scope first: analyze/job timeouts + queue bounds, artifact
  size caps (`services/artifact_guard.py`), paste payload limits, rate limiting on
  Ollama-backed endpoints. Exit: documented limits + tests for each bound.
- **C3 — Multi-model per-stage choice.** Analysis wizard: per-stage model select
  (stages 3/4/5 may use different Ollama models), persisted in investigation state.
- **C4 — Paste-box storage through a boundary batch** (tracker) for manifest/purge
  parity; sanitization must stay (see `ingest_json_notables` docstring).

### Phase D — Externals & wait-states (P3)

- **D1 — Vercel handoff (blocked on Justin).** Assemble the doc *now* so the unblock is
  a conversation, not a project: env vars (`DATABASE_URL`, `API_KEY`, `CORS_ORIGINS`,
  `ENABLE_DOCS` off), the injection-routing question (B4), deploy.yml smoke
  expectations, and the rotation history from the security tracker.
- **D2 — Live Splunk rehearsal (blocked on credentials).** Turnkey checklist:
  `SEARCH_BACKEND=splunk` + `SPLUNK_URL`/`SPLUNK_TOKEN`, run the mid-loop search-one
  scenario, verify CSV normalization + boundary-latch interplay.
- **D3 — User-side hygiene (not automatable):** Safari history scrub, thread-deletion
  decision (security tracker).

**Recommended order:** A → B (fast, compounding wins) while drafting C1's plan and D1's
doc in parallel; C builds in the sessions after. A1 is the only item with live exposure
risk — do it first.

### Hardening limits (signed off Sept 30, 2026)

| Limit | Default | Enforced at | Over-limit |
|---|---|---|---|
| Analyze wall-clock | 300 s | `api/routes/analyze.py` ollama call (worker thread + timeout) | 504, nothing persisted |
| Tool job queue | 100 queued | `api/routes/tools.py` job admission (`POST /api/execute`) | 429 |
| Artifact size | 50 MiB (= `SPLUNK_BOUNDARY_MAX_BYTES`) | `services/splunk_boundary.validate_file` (fail-closed report) | 413-equivalent rejection |
| Paste payload | 5 MiB raw text | `api/routes/notables.py` `paste_notable` entry | 413 |
| Ollama-backed POSTs | 30/min per client IP | rate-limit middleware in `api/main.py` (pre-rewrite canonical paths) | 429 |

(Signed off as proposed. C2.1.3 note: `services/artifact_guard.py` has no importers —
the live fail-closed enforcement of the size cap is `validate_file` in
`splunk_boundary.py`, so the limit lands there; the dead module stays untouched.)
