# SOC Platform — Development Plan

*Last updated: 2026-09-28. Owner: Dalton Lewis · Repo: `swinneyj/soc-plaform`*

---

## 1. Where we are (snapshot)

- **Code state:** `dev-dalton` at `442319d` — Phase 1 hardening ✅ and Phase 2 per-card AI evidence verdicts ✅ (incl. confidence-hint polish) pushed; CI green on Python 3.14 + 3.9.
- **Data state:** one shared **Neon** dataset — local API, Vercel prod, and Justin's instance all read/write the same database. Test rows must be clearly marked and deleted the same session; nothing destructive without explicit confirmation.
- **Runtime:** one-command startup via `scripts/start` (Ollama + BWS preflight + daemonized API on `127.0.0.1:8000`), Ollama `llama3.1:latest`, Neon Postgres (cloud) — app ports loopback-only. Secrets: BWS vault is source of truth (`scripts/pull-secrets` regenerates `.env`); credential-bearing keys live in the **macOS Keychain** (`scripts/secrets-keychain`), not in plaintext `.env`.
- **Test suite:** 141 pure-unit tests, <1 s runtime, dual-runtime (3.14 + 3.9) matrix in CI, covering evidence ledger validation, AI-derived direction scoring, per-card verdict parsing/precedence, question-driven follow-ups, model-tag resolution, closure gating, confidence caps/hints, loop-status transitions, boundary/latch, and report paths.
- **Known debt:** analyst-facing judgment remnants (Phase 4), ops backlog (Phase 5: backups, retention, auth activation), `web/Old` deletion + Vercel env-var handoff pending Justin.

### Collaboration hazard (read this first)
Justin has twice replaced repo history with fresh single-commit snapshots ("updated project files from WIndows"). Any local line not pushed is at risk of being orphaned. **Rule: push early, push often; re-verify his snapshots against the test suite before adopting them** (`pytest` catches regressions in <1 s).

---

## 2. Immediate actions

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
- **Scoring:** `_infer_direction_from_analysis` becomes the fallback; per-card `ai_direction` takes precedence when present. Aggregate rationale feeds the Investigative Analysis section.
- **UI:** evidence timeline shows the AI's per-card verdict chip with rationale tooltip; analyst still enters nothing but execution facts.
- **Tests:** prompt contract (JSON block present), parser (malformed → fallback), scoring precedence.
- **Polish (done):** `confidence_delta_hint` nudges disposition confidence ±0.02 per structured verdict, capped at ±0.06 aggregate, applied before disposition caps and the final clamp (hints refine but never outweigh the ledger or breach guardrails). Serializer exposes `confidence_hints` counts; UI shows a distinct violet **EVIDENCE JSON** chip (vs indigo **AI ASSESSMENT**) plus the hint on evidence cards.

**Size:** M–L. **Depends on:** nothing; can start after Phase 1 helper de-dup (touches the same code).

---

## 5. Phase 3 — Read-only Splunk REST integration

Turn the paste-driven loop into a one-click loop. **Read-only first** (search + results fetch; no writes to Splunk).

- **Auth/config:** `SPLUNK_URL`, `SPLUNK_TOKEN` (bearer) in `.env`; session-key flow only as fallback. Token stored in macOS keychain or env, never in DB.
- **Flow:** `POST /api/splunk/search-one {case_id, query_title}` → substitute placeholders (`$host$`, `$user$`…) from the case's notable fields → `search/jobs` (blocking mode, `max_count<=1000`) → fetch results → return raw rows for the analyst to review → one click saves them into the evidence ledger with `result_status` derived from the job outcome (0 rows ⇒ `no_results`; error ⇒ `query_failed`).
- **Guardrails:** per-case concurrency cap (1 running job), 60 s timeout, query text logged to the audit trail, explicit "SPL auto-run" label on resulting evidence rows (`source_system=splunk_auto`).
- **Non-goals (v1):** saved-search management, index writes, ES notable updates, multi-cluster.
- **Tests:** placeholder substitution (pure), job-outcome → status mapping (pure), endpoint with mocked Splunk HTTP.

**Size:** L. **Depends on:** Phase 1 API tests (so the ledger path is pinned before automating it).

---

## 6. Phase 4 — Judgment-flow audit (same principle, other surfaces)

Sweep remaining analyst-judgment surfaces with the evidence-model lens:

- Closure-note generation: confirm the operator cannot pre-set disposition fields the AI should derive.
- Triage verdict entry on promote: baseline confidence semantics — document or derive.
- Inquiry resolution remnants in Phase 2 state (analyst `question_resolution` values still stored): make them advisory-only, audited, or remove.

**Size:** S–M each. **Exit criteria:** the only analyst inputs anywhere are execution facts and observations.

---

## 7. Phase 5 — Operations & production readiness (backlog)

- **Auth:** the dashboard/API are unauthenticated on loopback. Before any non-localhost exposure: session auth + role split (analyst vs admin).
- **Backups:** `pg_dump` cron for `soc_platform`; document restore.
- **Service hardening:** review launchd plists (restart throttling, log rotation for `soc-api.log` / Ollama logs).
- **Multi-model:** model selector already exists in UI; expose per-stage model choice (analysis vs closure) and record it per iteration (DB default now `llama3.1:latest`).
- **Data retention:** `analysis_results` grows unbounded; add retention/pruning policy (e.g., keep last N per case + all closure-linked).

---

## 8. Verification strategy (applies to all phases)

1. Pure-unit tests for any new logic (no DB/Ollama) — keep the suite under ~1 s.
2. Contract tests for prompts and external payload shapes.
3. Live rehearsal before each merge to `main`: paste → promote → evidence (incl. empty no-results) → analyze → follow-up phase → closure gate.
4. `pytest` immediately after adopting any upstream snapshot from Justin.

---

## 9. Suggested sequence

```
Push queued commits → Phase 1 (CI + API tests + docs) → Phase 2 (per-card verdicts)
→ Phase 3 (Splunk read-only) → Phase 4 (judgment audit) → Phase 5 (ops)
```

Phases 2 and 3 are independent of each other after Phase 1; pick by appetite — Phase 2 deepens the analysis model, Phase 3 removes the manual paste bottleneck.
