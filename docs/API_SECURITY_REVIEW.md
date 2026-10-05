# API Integration Security Review — Findings & Optimizations

**Date:** 2026-10-05 · **Scope:** `api/` (FastAPI surface), `web/utils/auth.js`, `services/`, tool-execution path in `api/routes/tools.py` · **Method:** static review, cross-checked against `docs/security-remediation-tracker.md` (prior remediations are not re-listed as new) · **No code changes made.**

## Verdict

The integration is **substantially hardened** — far past the "no auth" baseline the tracker started from. What follows are the gaps that remain. Nothing here is an unauthenticated RCE; the top items are information-disclosure and auth-hygiene issues, plus two design seams worth an explicit decision.

### Confirmed solid

- **No shell injection in tool execution**: `subprocess.run([...], shell=False)` with argv lists; `tool_path` is resolved from the server-side registry (`Commander_Registry.json`), not client input (`api/routes/tools.py:86-107, 249-265`).
- **Parameterized DB access**: all API routes use SQLAlchemy ORM `.query().filter(...)`; the only f-string SQL lives in maintenance scripts with internal table names (`scripts/migrate_sqlite_to_postgres.py`, `scripts/attic/`).
- **Credential hygiene**: scrypt/PBKDF2-600k hashing with `hmac.compare_digest`; session tokens stored as sha256; CSRF double-submit compared with `compare_digest` (`api/auth.py:108-130, 206`).
- **Caps and timeouts**: paste 5 MB, job queue 100 (in-memory **and** persisted count), analyze 300 s wall clock, search-one per-case inflight 1 + 60 s, tool exec 300 s.
- **Boundary**: Splunk latch defaults to `quarantined`; report/artifact downloads resolve + containment-check paths.
- **CORS** explicit origins, `allow_credentials=False`; `/docs` + `/openapi.json` gated behind `ENABLE_DOCS=1`.

## Findings

### F1 — MEDIUM: raw exception text in 500 bodies (unauthenticated in API-key mode)

`api/main.py:257-262` — the global handler returns `{"detail": str(exc)}`, and ~40 route handlers do `raise HTTPException(500, detail=str(e))` (notably `analyze.py:637`, `closure.py`, `evidence.py`, `notables.py`, `rules.py`, `triage.py`). In API-key mode **reads are ungated by contract**, so anyone who can reach port 8000 can trigger a 500 and read internal error text: SQLAlchemy/psycopg messages carry SQL fragments, table names, host/port; Ollama errors can carry `OLLAMA_URL`.
**Fix:** return a generic `{"detail": "Internal error"}` + log the real exception server-side with a request id; sweep the route-level `str(e)` 500s to the same pattern (keep 4xx details where they're user-input validation).

### F2 — MEDIUM: API key accepted via `?api_key=` query parameter

`api/auth.py:43` (`x_api_key == _API_KEY or api_key == _API_KEY`), documented as "smoke-test convenience". Query-param credentials land in access logs, browser history, `Referer` headers, and proxy logs — a standing leak channel for a credential that (via SOC_CONFIG, F3) grants tool execution. `web/utils/auth.js:26-30` adds a client-side `?apiKey=` fallback with the same history-leak property.
**Fix:** deprecate the query param (header-only), or gate it behind a dev-only env flag (`AUTH_ALLOW_QUERY_KEY=1`) that fails closed; keep the smoke tests on the header.

### F3 — LOW (accepted, restated): UI access ≡ full write access

`api/main.py:287` injects `window.SOC_CONFIG = {"apiKey": …}` into every served `index.modular.html`. Documented as inherent to browser-side auth and acceptable on localhost/Tailscale — but it means **any process or person that can load the page can exfiltrate the key and then execute any registered tool as the host user**. Combined with F2, the key is also the only thing standing between the LAN and `/api/execute`.
**Keep accepted only while exposure stays localhost/Tailscale.** If exposure ever widens, this design must be replaced (token exchange / session auth for the UI).

### F4 — MEDIUM (intent-dependent): RBAC covers only 9 endpoints

`require_role("admin")` exists only on the Splunk-boundary (2) and tools/registry (7) endpoints. Every other mutating route — case create/update/**delete**, batch-delete, evidence save/**delete**, notables, promote, closure, rules CRUD, code-review, analyze, paste — enforces only "authenticated + CSRF" in session mode. An **analyst**-role session can delete cases, evidence, and rules.
**Action:** confirm this matches SESSION_AUTH_PLAN §3's "admin families" intent. If deletion families were meant to be admin-only, the dependency is missing; if analyst-deletes are intended, document the matrix explicitly.

### F5 — MEDIUM: login has no brute-force protection and a username-enumeration timing side channel

`api/routes/auth.py:55-58`: `if not user or not user.is_active or not auth.verify_password(...)` short-circuits when the user doesn't exist, so a **valid username costs a full scrypt/PBKDF2 run and an invalid one returns in microseconds** — reliable username enumeration by timing. `/api/auth/login` is exempt from the gate (necessarily) and has no rate limit or lockout; the rate limiter (`api/main.py:225-235`) covers only 5 Ollama-backed POST paths.
**Fix:** run `verify_password` against a dummy hash when the user is missing (constant-time user resolution); add login-attempt throttling (per-IP + per-username, reuse the limiter infra).

### F6 — MEDIUM: child tool processes inherit the full parent environment

`api/routes/tools.py:127` passes `env=os.environ.copy()` to every tool run. Every registered tool — and anything it shells out to — can read `DATABASE_URL` (live Neon password), `API_KEY`, `OLLAMA_API_KEY`; and tool stdout/stderr is persisted in `ToolRun` rows and returned to the client, so a tool that echoes its env leaks secrets into the API response and the DB.
**Fix (landed, C2.1.6):** `_tool_env()` in `api/routes/tools.py` builds an explicit allowlist (`PATH`, `HOME`, `COMMANDER_BOOT`, plus anything named in `_TOOL_ENV_PASSTHROUGH`) and is passed to all three `subprocess.run` sites in the file — registered-tool execution (sync and async), the catalog-regression runner, and the registry-reload indexer. `tests/test_api_key_gate.py::test_tool_subprocess_env_is_minimal` proves `DATABASE_URL`/`API_KEY`/`OLLAMA_API_KEY` are unreadable by a spawned tool even when set in the API process.

### F7 — LOW: `uvicorn.run(host="0.0.0.0")` binds all interfaces

`api/main.py:300`. The "localhost/Tailscale-only" posture rests entirely on the macOS firewall/Tailscale, not the app. Prefer `127.0.0.1` by default with an explicit `API_BIND` env override (Tailscale users set it deliberately), so a forgotten firewall rule doesn't expose an un-gated API (API-key mode leaves reads open).

### F8 — LOW: rate-limiter state is unbounded and coverage is narrow

`_RATE_HITS` (`api/main.py:214`) prunes stale timestamps per IP but never deletes the IP key — rotating source IPs grow memory without bound. It's per-process (fine for the single-worker launcher) and covers only 5 paths: login, case creation, evidence upload, Splunk admission, and all other mutations are unlimited.
**Fix:** periodic key sweep (or `OrderedDict` LRU); extend admission to login (F5) and cheap mutating endpoints.

### F9 — MEDIUM: no global request-body size limit

Only the paste endpoint enforces `PASTE_MAX_BYTES` (5 MB). Evidence-batch, notables, rules-import and other JSON endpoints parse unbounded bodies into memory — trivial memory-exhaustion DoS from any client that can reach the API.
**Fix:** a Starlette middleware rejecting `Content-Length` (and chunked overage) above a sane cap (e.g. 10–20 MB) before parsing.

### F10 — LOW: argument injection into tool CLIs

Args are passed as an argv list (no shell — good), but client-controlled argument **values** beginning with `--` can be reinterpreted as flags by the tool's own argparse. Low severity given admin gating; sanitize values or document the contract per tool.

### F11 — LOW (accepted, documented): Python 3.9 branch pins vulnerable starlette/fastapi

`requirements.txt` header records PYSEC-2026-248/2281/2280 and PYSEC-2026-1845 as accepted risk on the `<3.10` pins; the production (≥3.10) branch is clean and pip-audit passed. CI still exercises the 3.9 matrix — retiring 3.9 from the matrix closes the item.

### F12 — LOW: expired `AuthSession` rows are never vacuumed

`_session_from_request` checks expiry but nothing deletes expired rows; minor DB growth over time. (No password-change endpoint exists, so revocation-on-password-change is moot today.)

### F13 — LOW: no security headers / no CSP on the served UI

The UI is served with only `Cache-Control: no-cache`; no CSP (the inline `SOC_CONFIG` bootstrap would need a nonce), no `X-Content-Type-Options`, and authenticated JSON responses lack `Cache-Control: no-store` (browsers may cache case data). Minor at current exposure; worth a pass before any wider rollout.

### F14 — LOW (latent): attic endpoints carry no admission checks at all — reactivation checklist

Audited every endpoint-bearing file in `scripts/attic/` (`closure_note_endpoint.py`, `fix_closure_endpoint.py`, `db_routes.py`, `update_api.py`; plus patchers `apply_closure_fix.py`, `fix_endpoint.py`, `update_closure_endpoint.py` that carry endpoint source as string literals). **None carries an inline admission check** — the only non-validation statuses are two 503 Ollama-availability probes in `db_routes.py:110,133` (dependency checks, not gates), and the inline checks that do exist are input validation (400 `rule_id and case_id required`), existence (404), template `KeyError` (400), and pydantic `Query(ge=, le=)` bounds. So reactivation cannot drift from `api/helpers/admission.py`'s boundary semantics with a competing inline check — the risk is the inverse: attic endpoints predate the gates and would **skip them entirely**:

- **No admission** — attic `/api/db/analyze` blocks on Ollama with no queue gate (live: `admission.queue_rejection`, `api/routes/tools.py:311`) and no wall-clock cap (live: `ANALYZE_TIMEOUT_S` 300 s → 504 in `api/routes/analyze.py`); closure-note accepts an unbounded raw `dict` body with no payload byte budget (live: `admission.payload_rejection`, `api/routes/notables.py:1315`); nothing consults the Splunk mode latch (`api/routes/splunk.py:62`) or the per-resource concurrency cap (`api/routes/splunk.py:125`).
- **No auth/CSRF** — attic endpoints declare no `require_api_key`/`require_role`/CSRF dependencies (`db_routes.py` depends only on `get_db`); they predate the auth scaffold (`api/auth.py`).
- **F1 regression vector** — `update_api.py` rewrites `api/main.py` including the **old** global handler `{"detail": str(exc)}` — the exact pattern F1's fix (293b366) eliminated. Re-running that patcher silently reintroduces raw exception text in 500 bodies; its `/api/db/rules` also swallows every exception to `[]`.

**Action if any attic endpoint is ever reactivated:** wire it through `api/helpers/admission.py` (measure inputs, raise the returned exception), attach the same auth/CSRF dependencies as its live siblings, keep the generic-500/`raise_internal` pattern, and never re-import the pre-F1 exception handler. The attic is currently unmounted — no module in `api/` imports it (verified by static search) — so this is latent, not live, exposure.

## Optimizations

1. **Cache actor resolution** — `resolve_actor` → `_session_from_request` runs 2 DB queries (AuthSession + User) on **every** `/api/*` request in session mode (`api/auth.py:167-186`). A short-TTL (30–60 s) in-memory `token_hash → (user_id, role, csrf)` cache, invalidated on logout, removes most of that load; the UI polls frequently.
2. **Cache the bootstrapped index HTML** — `/index.modular.html` re-reads the file and re-injects the config on every page load (`api/main.py:271-294`). Cache keyed on `(_API_KEY, mtime)`.
3. **Bound concurrent tool executions separately from the queue cap** — up to 100 RUNNING jobs each block an anyio threadpool thread (default ~40 tokens) for up to 300 s; beyond ~40 concurrent runs, sync endpoints starve. A running-semaphore (e.g. 4–8) keeps the queue at 100 but caps simultaneous subprocesses. **Landed:** `TOOL_CONCURRENCY_MAX` (env, default 6) bounds simultaneous subprocesses in `api/routes/tools.py`; `execute_tool_async` is now a coroutine, so jobs waiting for a permit park on the event loop (zero anyio threadpool tokens) and only the subprocess run itself — under a permit — occupies one. Permit pools are cached per event loop (`_tool_run_semaphores`) because asyncio primitives bind to their loop; the job queue stays at `JOB_QUEUE_MAX`=100. A job deleted while parked is re-checked once a permit is acquired, so a cancelled job never starts its subprocess. Regression tests: `tests/test_api_key_gate.py::test_tool_execution_concurrency_capped_by_semaphore` (end-to-end: 5 concurrent requests with the cap at 2 never run more than 2 subprocesses at once, all jobs complete), `::test_parked_tool_jobs_hold_no_threadpool_tokens` (the starvation fix: 4 live jobs with the cap at 2 hold exactly 2 of the ~40 threadpool tokens), `::test_deleted_while_waiting_job_never_starts_subprocess`, `::test_tool_run_semaphore_pool_is_per_event_loop`.
4. **Cheaper admission count** — `_unfinished_job_count()` fetches every `ToolRun` row on each `/api/execute` (`api/routes/tools.py:55-77`); a `func.count()` filtered on status suffices at scale.
5. **Narrow `_tool_artifact_snapshot()`** — it walks 5 directory trees before *and* after every tool run (`api/routes/tools.py:109-131`); scoping to registry-declared output dirs makes small-tool runs cheaper.
6. **Frontend error rendering** — verify the UI renders `detail` via `textContent`, not `innerHTML`; with F1's `str(exc)` in the payload, innerHTML rendering would turn server errors into an XSS vector.

## Already tracked (not new)

Path-traversal fix (#15), docs gating (#14), CORS tightening (#7), auth scaffold + activation (#5/#6), dependency audit (#11), Splunk boundary latch, keychain secret storage (#3) — all in `docs/security-remediation-tracker.md`.
