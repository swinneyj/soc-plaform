# SOC Platform — Development Plan

*Last updated: 2026-09-29. Owner: Dalton Lewis · Repo: `swinneyj/soc-plaform`*

---

## 1. Where we are (snapshot)

- **Code state:** `dev-dalton` at `8ab1044` — Phase 1 hardening ✅, Phase 2 per-card AI evidence verdicts ✅ (incl. confidence-hint polish), and Phase 3 read-only Splunk integration ✅ pushed (mock loop + `RealSplunkBackend` REST connector, the mid-loop search-one harness, Run All + inline run status, and the Sept 29 live-smoke fixes in §8); CI green on Python 3.14 + 3.9.
- **Data state:** one shared **Neon** dataset — local API, Vercel prod, and Justin's instance all read/write the same database. Test rows must be clearly marked and deleted the same session; nothing destructive without explicit confirmation.
- **Runtime:** one-command startup via `scripts/start` (Ollama + BWS preflight + daemonized API on `127.0.0.1:8000`), Ollama `llama3.1:latest`, Neon Postgres (cloud) — app ports loopback-only. Secrets: BWS vault is source of truth (`scripts/pull-secrets` regenerates `.env`); credential-bearing keys live in the **macOS Keychain** (`scripts/secrets-keychain`), not in plaintext `.env`.
- **Test suite:** 211 tests (pure-unit + TestClient), <2 s runtime, dual-runtime (3.14 + 3.9) matrix in CI, covering evidence ledger validation, AI-derived direction scoring, per-card verdict parsing/precedence, question-driven follow-ups (incl. targeted-evidence question resolution), model-tag resolution, closure gating, confidence caps/hints, loop-status transitions, boundary/latch, report paths, the notable-parsing pipeline (`evidence_service`), the mid-loop search-one loop harness (phases 2–5 with real executions), the playbook family guard, `RealSplunkBackend` REST contract tests (hermetic via an injectable session), and UI regressions via a zero-dependency node vm harness (`tests/ui_regression/`) driving the real `runSplunkSearch`.
- **Known debt:** analyst-facing judgment remnants (Phase 4: promote-time triage verdicts + `question_resolution`), ops backlog (Phase 5: backups, retention, auth activation), `web/Old` deletion + Vercel env-var handoff pending Justin, and dead monolith remnants pending cleanup (Sept 29: the corrupted legacy tail in `index.html` was stripped, the 9 zero-reference root one-shots moved to `scripts/attic/`, and `api/main.py` bare `print()` error paths converted to structured `logging` — last-resort handler keeps stderr so log redirects are unaffected; `web/app.js` archival and `web/Old` deletion remain Justin's call per §3).

### Collaboration hazard (read this first)
Justin has twice replaced repo history with fresh single-commit snapshots ("updated project files from WIndows"). Any local line not pushed is at risk of being orphaned. **Rule: push early, push often; re-verify his snapshots against the test suite before adopting them** (`pytest` catches regressions in <1 s).

---

## 2. Immediate actions

*Owner triage Sept 28, 2026: all deferred as non-blocking (see tracker "Open items, in order") — Phase 3 can start now.*

1. **Vercel env vars (Justin):** new Neon `DATABASE_URL` + `API_KEY` — the classic miss that breaks the next deploy; deploy.yml's `/api/health` smoke test catches it loudly.
2. **API-gate activation decision (Dalton + Justin, ~10 min):** `API_KEY` is in the vault; setting it in Vercel + getting `SOC_CONFIG.apiKey` into the browser flips the gate on (unlocks `restricted` Splunk-boundary mode too).
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
| Stray-file guardrail | ✅ `*.bak`/Copy patterns pre-existing; `.pytest_cache/` added `be2143f`. `web/Old/**` deletion pending Justin's call |

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
- **UI (shipped):** "Run in Splunk" button on Stage-2 supportive query cards and Phase 2 follow-up cards; results land in the card notes and the Evidence Timeline ledger immediately (investigation state rebuilt on save). Sept 29: **Run All** on Stage 2 (sequential, skips busy cards, summary banner) and non-blocking inline per-card run status (`running → Saved ✓ / Run failed`) replacing modal alerts on every run path.
- **Auth/config (delivered with the connector):** `SPLUNK_URL`, `SPLUNK_TOKEN` (bearer) read from env at construction (BWS/keychain injection, never in DB); the connector is read-only by design — no job management, no writes. Session-key flow rejected as unnecessary complexity.
- **Non-goals (v1):** saved-search management, index writes, ES notable updates, multi-cluster.
- **Tests:** placeholder substitution, outcome mapping, and row summarization (pure, `tests/test_search_backend.py`); `RealSplunkBackend` contract (same file) — export request shape (URL/bearer/timeout/payload), `search ` prefixing vs generating commands, CSV normalization incl. epoch→ISO, stats-row shape defaults, HTTP-error and transport-error wrapping, `execute_for_case` payload, factory selection — all hermetic via a duck-typed injectable session; endpoint via TestClient — substitution, `no_results`/`query_failed` mapping, per-title replace, 404/422 guards, concurrency 409, timeout (`tests/test_api_analyze_flow.py`); mid-loop harness `TestSearchOneMidLoop` (phases 2–5 interleaved with real search-one executions — exactly one `splunk_auto` row per (case, query), auto-evidence consumed by the next phase's prompt) and embedded `earliest=`/`latest=` window parsing (5-tuple `_parse_spl`; embedded windows override the caller's).

**Size:** L. **Depends on:** Phase 1 API tests (so the ledger path is pinned before automating it). **Remaining:** live rehearsal against a real Splunk instance once the API key arrives (set `SPLUNK_URL`/`SPLUNK_TOKEN`, flip `SEARCH_BACKEND=splunk`).

---

## 6. Phase 4 — Judgment-flow audit (same principle, other surfaces)

Sweep remaining analyst-judgment surfaces with the evidence-model lens:

- Closure-note generation: **done (Sep 28, 2026)** — the disposition is now derived server-side from the evidence-backed `investigation_state.provisional_disposition` (`closure_service.derive_closure_disposition`); the caller's value is advisory/audited only (`operator_disposition` + `disposition_conflict` in the response) and the UI disposition select is disabled. Rule-required `field_values` render only under an attributed "Operator-Recorded Closure Fields (execution facts, not conclusions)" header and can never reach the conclusion sentence — pinned by tests in `test_closure_gate.py` / `test_api_analyze_flow.py`.
- Triage verdict entry on promote: baseline confidence semantics — document or derive. *(open)*
- Inquiry resolution remnants in Phase 2 state (analyst `question_resolution` values still stored): make them advisory-only, audited, or remove. *(open — partially displaced Sept 29: substantive evidence carrying `target_questions` now resolves its targeted inquiries automatically, so the analyst-entered flag is no longer the only resolution path and the loop can converge on auto-collected evidence alone.)*

**Size:** S–M each. **Exit criteria:** the only analyst inputs anywhere are execution facts and observations.

---

## 7. Phase 5 — Operations & production readiness (backlog)

- **Auth:** the dashboard/API are unauthenticated on loopback. Before any non-localhost exposure: session auth + role split (analyst vs admin).
- **Backups:** `pg_dump` cron for `soc_platform`; document restore.
- **Service hardening:** review launchd plists (restart throttling, log rotation for `soc-api.log` / Ollama logs).
- **Multi-model:** model selector already exists in UI; expose per-stage model choice (analysis vs closure) and record it per iteration (DB default now `llama3.1:latest`).
- **Data retention:** `analysis_results` grows unbounded; add retention/pruning policy (e.g., keep last N per case + all closure-linked).

---

## 8. Known issues

- **SPL follow-up phase degradation (reported Sept 28, 2026 — RESOLVED Sept 28):** after Phase 3 the iterative investigation stopped surfacing new queries and the loop view degraded ("basically stopped working overall"). Diagnosed with the loop-stress harness (`tests/test_api_analyze_flow.py::TestFollowUpLoopStress`, which auto-drives 7 follow-up iterations against a 2-template playbook and asserts every phase returns cards carrying a *fresh* SPL; extended Sept 28 with `TestSearchOneMidLoop`, which interleaves real `POST /api/splunk/search-one` executions between phases and asserts the auto-collected `splunk_auto` evidence executes successfully against the mock backend, ledgers exactly once per (case, query), and is consumed by the next phase's prompt — covering the full analyst loop: propose → execute → consume). Root causes and fixes: (1) re-check variants cloned the template SPL verbatim — same query under new phase-stamped titles = "no new queries" → variants now re-scope the time window (`earliest=-<phase>h`, `_rescope_variant_spl`); (2) the Phase 3+ builder dead-ended once targets emptied while the loop still needed work → it now falls back to hypothesis-verification re-checks whenever `loop_status != ready_for_closure` (empty cards only when the loop has converged); (3) questions raised by the current analysis were not targets until the next iteration → `current_questions` merge; (4) `ai_finding_type` per-card verdicts persisted by `/db/analyze` were written but never read back, so post-save state rebuilds degraded every entry to neutral ("evidence marked neutral only") → rebuilds now honor persisted per-card verdicts. Known nuance: re-saving evidence replaces rows and clears stored verdicts until the next analysis re-assesses the new results (by design).

- **Modular analysis-tab crash + blocking run alerts (reported Sept 29 from the live UI smoke — RESOLVED Sept 29):** the modular tab died blank when phase-2 cards rendered (`AnalysisTab` read `phase2CoverageNotes` from a prop `index.modular.html` never passed, plus an undeclared `supportiveSaveBusy`), and `runSplunkSearch`'s success `alert()` can wedge embedded webviews mid-loop. Fixed: root state + bindings + `?v=` cache-buster bumps; inline per-card run status (`running → Saved ✓ / Run failed`) replaces the modal on every path with Run disabled while running; locked by a zero-dependency node vm harness (`tests/ui_regression/run_splunk_search_status.mjs`) that drives the real `runSplunkSearch` and asserts chip transitions and zero `alert()` calls across 5 scenarios.

- **Playbook family hijack (reported Sept 29 from the live UI smoke — RESOLVED Sept 29):** `/api/db/analyze`'s fuzzy `supportive_rules.json` matcher adopted an unrelated catalog entry (MOCK-RULE-001 matched `sso_brute_force_pingfederate` on shared anchor tokens) even for rules that own DB-backed `SupportiveQuery` rows, zeroing `supportive_playbook_available` and hiding the Stage 4 follow-up generator mid-investigation. Fixed: fuzzy catalog adoption now only fires when the case's rule has no DB-backed queries (`api/main.py`); regression tests `TestPlaybookFamilyGuard` include a counter-test that playbook-less imported notables still adopt catalog families.

---

## 9. Verification strategy (applies to all phases)

1. Pure-unit tests for any new logic (no DB/Ollama) — keep the suite under ~1 s.
2. Contract tests for prompts and external payload shapes.
3. Live rehearsal before each merge to `main`: paste → promote → evidence (incl. empty no-results) → analyze → follow-up phase → closure gate.
4. `pytest` immediately after adopting any upstream snapshot from Justin.

---

## 10. Suggested sequence

```
Push queued commits → Phase 1 (CI + API tests + docs) → Phase 2 (per-card verdicts)
→ Phase 3 (Splunk read-only) → Phase 4 (judgment audit) → Phase 5 (ops)
```

Phases 2 and 3 are independent of each other after Phase 1; pick by appetite — Phase 2 deepens the analysis model, Phase 3 removes the manual paste bottleneck.
