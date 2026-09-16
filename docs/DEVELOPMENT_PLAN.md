# SOC Platform — Development Plan

*Last updated: 2026-09-16. Owner: Dalton Lewis · Repo: `swinneyj/soc-plaform`*

---

## 1. Where we are (snapshot)

- **Code state:** `main` at `88b6153` — Justin's Windows snapshot (`c989730`) + five local commits queued for push (tests, model auto-resolution, prompt consolidation, resolved-model echo, closure-gate tests).
- **Data state:** database wiped clean (0 cases / notables / analyses); rule playbooks, correlation rules, and placeholder aliases preserved. Nothing sensitive on disk; Desktop demo kit removed.
- **Runtime:** API + dashboard on `127.0.0.1:8000` (launchd), Ollama `llama3.1:latest`, Postgres `:5432` — all loopback-only.
- **Test suite:** 46 pure-unit tests, ~0.3 s runtime, covering evidence ledger validation, AI-derived direction scoring, Phase 3+ question-driven follow-ups, model-tag resolution, closure gating, confidence caps, and loop-status transitions.
- **Known debt:** no CI, drifted docs, `services/analysis_service.py` still carries duplicated helpers (grounding/extraction) with `api/main.py` twins, analyst-facing judgment surfaces remain in the closure flow.

### Collaboration hazard (read this first)
Justin has twice replaced repo history with fresh single-commit snapshots ("updated project files from WIndows"). Any local line not pushed is at risk of being orphaned. **Rule: push early, push often; re-verify his snapshots against the test suite before adopting them** (`pytest` catches regressions in <1 s).

---

## 2. Immediate actions

1. **Push the five queued commits** via GitHub Desktop (Push origin (5)).
2. **Notify Justin:** his snapshot rewrote history — collaborators need to re-clone; also flag the now-fixed first-run 500 (`llama3.1:8b` not installed) so he pulls before his next demo.
3. **Adopt-branch habit going forward:** do substantial work on `feature/*` branches, merge to `main` only after `pytest` passes.

---

## 3. Phase 1 — Hardening (small, high-value)

| Item | Description | Size |
|---|---|---|
| CI workflow | GitHub Actions: `pip install -r requirements.txt pytest` on every push/PR; green badge on README | S |
| API-level tests | FastAPI `TestClient` coverage for `/db/triage/{id}/evidence`, `/db/analyze` (mocked Ollama), promote/delete flows | M |
| Doc alignment | README/SOP/SETUP still say `llama3.1:8b`; document auto-resolution + `OLLAMA_MODEL` env override; remove references to removed dropdowns | S |
| Helper de-dup | `analysis_service.py` vs `api/main.py` still twin `_extract_phase2_queries` / `_ground_phase2_queries` / `_normalize_phase2_text`; route main.py imports through the service | M |
| Stray-file guardrail | `.gitignore` for `*.bak`, `* - Copy.*`, `.pytest_cache/`; delete `web/Old/**` if Justin agrees | S |

**Exit criteria:** CI green on GitHub; no duplicated prompt/grounding logic; docs match runtime behavior.

---

## 4. Phase 2 — Per-card AI evidence verdicts

Complete the "analyst brings logs, AI decides" principle at the per-entry level.

- **Design:** extend the analysis prompt to return a `PHASE2_EVIDENCE_JSON` block: `[{title, direction: supports|refutes|neutral, rationale, confidence_delta_hint}]` for each evidence card it reviewed.
- **Storage:** new nullable columns/JSON on `SupportiveQueryResult.raw_result` (`ai_direction`, `ai_rationale`, `ai_assessed_at`) — legacy rows stay valid with nulls.
- **Scoring:** `_infer_direction_from_analysis` becomes the fallback; per-card `ai_direction` takes precedence when present. Aggregate rationale feeds the Investigative Analysis section.
- **UI:** evidence timeline shows the AI's per-card verdict chip with rationale tooltip; analyst still enters nothing but execution facts.
- **Tests:** prompt contract (JSON block present), parser (malformed → fallback), scoring precedence.

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
