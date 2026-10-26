# API Integration Security Review — Findings & Optimizations

**Date:** 2026-10-05 · **Scope:** `api/` (FastAPI surface), `web/utils/auth.js`, `services/`, tool-execution path in `api/routes/tools.py` · **Method:** static review, cross-checked against `docs/security-remediation-tracker.md` (prior remediations are not re-listed as new) · **No code changes made.**

> **Status sync 2026-10-23 (re-verified against the code, not these findings).** F1, F2, F5, F6,
> F7, F9, F12, F13 and F15 are **resolved** (F13 narrow: no `script-src` policy); F8 is
> **partially** resolved; F10 was **downgraded to informational** after checking the argv
> builder; F3/F4 are accepted/decided. The middleware
> they were fixed in now lives in `api/middleware.py` (E.5 split, commit `0a9de01`), not
> `api/main.py` — line references below are current. Regression coverage is a dedicated block in
> `tests/test_api_key_gate.py` (F2 at :88-107, F9/F8/F13/F12 at :1611-1694). One new finding
> was added by the re-verification and **fixed the same day**: **F15 — CORS preflight rejects
> `X-CSRF-Token`**. Headings carry their status.

## Verdict

The integration is **substantially hardened** — far past the "no auth" baseline the tracker started from. What follows are the gaps that remain. Nothing here is an unauthenticated RCE; the top items are information-disclosure and auth-hygiene issues, plus two design seams worth an explicit decision.

**Where it stands on 2026-10-23:** F1, F2, F5, F6, F7, F9, F12, F13 and F15 are **resolved**
(F13 deliberately narrow — see its entry for why a `script-src` policy would be decorative on
this UI); F8 is **partially** resolved (growth fixed, coverage unchanged by decision); F10 is
**informational** — the argv builder binds every value to an explicit option, so there is no
reachable injection; F3 and F4 are **accepted/decided** rather than fixed; F11 is closed by
retiring the 3.9 leg; F14 stays **latent** while the attic is unmounted. What remains is not a
list of unfixed vulnerabilities — it is one narrowed control, two deliberate design
boundaries, and three accepted risks, each with its rationale recorded.

### Confirmed solid

- **No shell injection in tool execution**: `subprocess.run([...], shell=False)` with argv lists; `tool_path` is resolved from the server-side registry (`Commander_Registry.json`), not client input (`api/routes/tools.py:86-107, 249-265`).
- **Parameterized DB access**: all API routes use SQLAlchemy ORM `.query().filter(...)`; the only f-string SQL lives in maintenance scripts with internal table names (`scripts/migrate_sqlite_to_postgres.py`, `scripts/attic/`).
- **Credential hygiene**: scrypt/PBKDF2-600k hashing with `hmac.compare_digest`; session tokens stored as sha256; CSRF double-submit compared with `compare_digest` (`api/auth.py:108-130, 206`).
- **Caps and timeouts**: paste 5 MB, job queue 100 (in-memory **and** persisted count), analyze 300 s wall clock, search-one per-case inflight 1 + 60 s, tool exec 300 s.
- **Boundary**: Splunk latch defaults to `quarantined`; report/artifact downloads resolve + containment-check paths.
- **CORS** explicit origins, `allow_credentials=False`; `/docs` + `/openapi.json` gated behind `ENABLE_DOCS=1`.

## Findings

### F1 — ✅ RESOLVED (2026-10-05, re-verified 2026-10-23): raw exception text in 500 bodies (unauthenticated in API-key mode)

`api/main.py:257-262` — the global handler returns `{"detail": str(exc)}`, and ~40 route handlers do `raise HTTPException(500, detail=str(e))` (notably `analyze.py:637`, `closure.py`, `evidence.py`, `notables.py`, `rules.py`, `triage.py`). In API-key mode **reads are ungated by contract**, so anyone who can reach port 8000 can trigger a 500 and read internal error text: SQLAlchemy/psycopg messages carry SQL fragments, table names, host/port; Ollama errors can carry `OLLAMA_URL`.
**Fix:** return a generic `{"detail": "Internal error"}` + log the real exception server-side with a request id; sweep the route-level `str(e)` 500s to the same pattern (keep 4xx details where they're user-input validation).
**Status:** landed — `api/helpers/errors.py` (`INTERNAL_ERROR_DETAIL`, `InternalError`,
`raise_internal`), request-id middleware, and a `HTTPException` handler that scrubs **any**
500 as a safety net. Re-verified 2026-10-23: the only remaining `detail=str(...)` calls in
`api/` are 4xx (404/400/503), which this finding explicitly keeps. Tests:
`tests/test_internal_errors.py`.

### F2 — ✅ RESOLVED (2026-10-23): API key accepted via `?api_key=` query parameter

`api/auth.py:43` (`x_api_key == _API_KEY or api_key == _API_KEY`), documented as "smoke-test convenience". Query-param credentials land in access logs, browser history, `Referer` headers, and proxy logs — a standing leak channel for a credential that (via SOC_CONFIG, F3) grants tool execution. `web/utils/auth.js:26-30` adds a client-side `?apiKey=` fallback with the same history-leak property.
**Fix:** deprecate the query param (header-only), or gate it behind a dev-only env flag (`AUTH_ALLOW_QUERY_KEY=1`) that fails closed; keep the smoke tests on the header.
**Status (2026-10-23):** fixed exactly that way — `require_api_key` and `request_has_api_key`
accept `?api_key=` only when `query_key_allowed()` (`AUTH_ALLOW_QUERY_KEY=1`), so a
query-only request 401s by default (`api/auth.py:41-89`). The frontend's `?apiKey=`
fallback still writes the parameter into history when used, but the deployment credential
path is header-only now. Tests: `tests/test_api_key_gate.py::test_execute_rejected_with_query_key_by_default`,
`::test_execute_accepted_with_query_key_when_flagged`; plus
`tests/test_api_analyze_flow.py::TestQueryKeyGate`.

### F3 — ACCEPTED (re-verified 2026-10-23): UI access ≡ full write access

`api/main.py:287` injects `window.SOC_CONFIG = {"apiKey": …}` into every served `index.modular.html`. Documented as inherent to browser-side auth and acceptable on localhost/Tailscale — but it means **any process or person that can load the page can exfiltrate the key and then execute any registered tool as the host user**. Combined with F2, the key is also the only thing standing between the LAN and `/api/execute`.
**Keep accepted only while exposure stays localhost/Tailscale.** If exposure ever widens, this design must be replaced (token exchange / session auth for the UI).

### F4 — DECIDED (2026-10-14): RBAC covers only 9 endpoints

`require_role("admin")` exists only on the Splunk-boundary (2) and tools/registry (7) endpoints. Every other mutating route — case create/update/**delete**, batch-delete, evidence save/**delete**, notables, promote, closure, rules CRUD, code-review, analyze, paste — enforces only "authenticated + CSRF" in session mode. An **analyst**-role session can delete cases, evidence, and rules.
**Decision (Oct 14, 2026):** analyst deletes retained intentionally (owner) — cleanup is analyst work; the matrix in SESSION_AUTH_PLAN §3 now records this explicitly with the rationale. Admin gate stays on tools/registry/boundary/jobs-delete; no code change.

### F5 — ✅ RESOLVED (2026-10-05, re-verified 2026-10-23): login had no brute-force protection and a username-enumeration timing side channel

`api/routes/auth.py:55-58`: `if not user or not user.is_active or not auth.verify_password(...)` short-circuits when the user doesn't exist, so a **valid username costs a full scrypt/PBKDF2 run and an invalid one returns in microseconds** — reliable username enumeration by timing. `/api/auth/login` is exempt from the gate (necessarily) and has no rate limit or lockout; the rate limiter (`api/main.py:225-235`) covers only 5 Ollama-backed POST paths.
**Fix:** run `verify_password` against a dummy hash when the user is missing (constant-time user resolution); add login-attempt throttling (per-IP + per-username, reuse the limiter infra).
**Status:** landed — every attempt pays one KDF run (`_dummy_password_hash()` for
missing/inactive users, `api/routes/auth.py:47,136`), and two independent sliding windows
(per-IP, per-username) 429 **before** user resolution (`_login_throttled`,
`api/routes/auth.py:68-119`; `LOGIN_MAX_FAILURES`/`LOGIN_WINDOW_SECONDS`). Tests: the F5
block in `tests/test_api_key_gate.py` (spy proofs, timing sanity, throttle, independent
windows, success clears, window expiry).

### F6 — ✅ RESOLVED (C2.1.6, re-verified 2026-10-23): child tool processes inherit the full parent environment

`api/routes/tools.py:127` passes `env=os.environ.copy()` to every tool run. Every registered tool — and anything it shells out to — can read `DATABASE_URL` (live Neon password), `API_KEY`, `OLLAMA_API_KEY`; and tool stdout/stderr is persisted in `ToolRun` rows and returned to the client, so a tool that echoes its env leaks secrets into the API response and the DB.
**Fix (landed, C2.1.6):** `_tool_env()` in `api/routes/tools.py` builds an explicit allowlist (`PATH`, `HOME`, `COMMANDER_BOOT`, plus anything named in `_TOOL_ENV_PASSTHROUGH`) and is passed to all three `subprocess.run` sites in the file — registered-tool execution (sync and async), the catalog-regression runner, and the registry-reload indexer. `tests/test_api_key_gate.py::test_tool_subprocess_env_is_minimal` proves `DATABASE_URL`/`API_KEY`/`OLLAMA_API_KEY` are unreadable by a spawned tool even when set in the API process.

### F7 — ✅ RESOLVED (2026-10-23): `uvicorn.run(host="0.0.0.0")` binds all interfaces

`api/main.py:300`. The "localhost/Tailscale-only" posture rests entirely on the macOS firewall/Tailscale, not the app. Prefer `127.0.0.1` by default with an explicit `API_BIND` env override (Tailscale users set it deliberately), so a forgotten firewall rule doesn't expose an un-gated API (API-key mode leaves reads open).
**Status (2026-10-23):** landed as specified — `host=os.environ.get("API_BIND", "127.0.0.1")`
(`api/main.py:205`), with `scripts/dev` already loopback-only. One deliberate exception:
`Dockerfile` keeps `0.0.0.0` because container port-mapping requires it, and that exposure is
bounded by the compose port map rather than by the app. No test: the value is read at launch,
so `pytest` never imports a bound server — verify with
`lsof -nP -iTCP:8000 -sTCP:LISTEN` after `scripts/dev` (expect `127.0.0.1`). `API_BIND` is not
yet listed in `.env.example`.

### F8 — ✅ PARTIALLY RESOLVED (2026-10-23): rate-limiter state is unbounded and coverage is narrow

`_RATE_HITS` (`api/main.py:214`) prunes stale timestamps per IP but never deletes the IP key — rotating source IPs grow memory without bound. It's per-process (fine for the single-worker launcher) and covers only 5 paths: login, case creation, evidence upload, Splunk admission, and all other mutations are unlimited.
**Fix:** periodic key sweep (or `OrderedDict` LRU); extend admission to login (F5) and cheap mutating endpoints.
**Status (2026-10-23) — half done:** the unbounded-growth half is fixed — every request on a
rate-limited path sweeps keys whose timestamps are all older than the window
(`api/middleware.py:252-254`), pinned by
`tests/test_api_key_gate.py::test_rate_limiter_sweeps_fully_stale_ips` (`api/middleware.py:256-265`). The coverage half is
unchanged: `_RATE_PATHS` still names five Ollama-backed POSTs (login is covered separately by
the F5 throttle, which is per-IP *and* per-username). Cheap mutating endpoints remain
unlimited by design — the queue cap, the body cap and the auth gate are the controls there.

### F9 — ✅ RESOLVED (2026-10-23): no global request-body size limit

Only the paste endpoint enforces `PASTE_MAX_BYTES` (5 MB). Evidence-batch, notables, rules-import and other JSON endpoints parse unbounded bodies into memory — trivial memory-exhaustion DoS from any client that can reach the API.
**Fix:** a Starlette middleware rejecting `Content-Length` (and chunked overage) above a sane cap (e.g. 10–20 MB) before parsing.
**Status (2026-10-23):** landed as `_body_size_cap` (`api/middleware.py:196-240`),
`MAX_BODY_BYTES` default **10 MiB**, registered outermost so it rejects before routing, auth
or parsing. A declared `Content-Length` is checked without reading a byte; an undeclared
(chunked) body is drained through the raw ASGI channel with a running total and replayed, so
a stream fails at the cap instead of exhausting worker memory. `/api/notables/paste` keeps
its tighter 5 MiB gate inside. Tests:
`tests/test_api_key_gate.py::test_body_cap_rejects_huge_declared_length`,
`::test_body_cap_allows_normal_mutation` (negative control). `MAX_BODY_BYTES` is not yet
listed in `.env.example`.

### F10 — INFORMATIONAL (downgraded 2026-10-23): argument injection into tool CLIs

Args are passed as an argv list (no shell — good), but client-controlled argument **values** beginning with `--` can be reinterpreted as flags by the tool's own argparse. Low severity given admin gating; sanitize values or document the contract per tool.
**Re-verified 2026-10-23 — not a reachable injection.** The argv builder is
`cmd.extend([f'--{key}', str(val)])` (`api/routes/tools.py:291-297`): every value is one argv
element bound as the *argument of an explicit option*, and every key is rendered as its own
`--`-prefixed token, so neither can split into extra argv entries. A value like `--dangerous`
reaches the tool as the value of `--target`, where argparse answers "expected one argument" —
a failed run, not a smuggled flag. The only residual is a tool that hand-parses `sys.argv` and
re-scans tokens (none in `Tools/` today). No code change; the per-tool contract note stands if
a tool ever stops using argparse.

### F11 — ✅ CLOSED (2026-10-14): Python 3.9 branch pinned vulnerable starlette/fastapi

`requirements.txt` header recorded PYSEC-2026-248/2281/2280 and PYSEC-2026-1845 as accepted risk on the `<3.10` pins; the production (≥3.10) branch is clean and pip-audit passed. **Closed Oct 14, 2026:** the 3.9 leg is out of the CI matrix (owner decision), so the vulnerable pins are unexercised marker-dead branches — retained for local legacy venvs, not extended.

### F12 — ✅ RESOLVED (2026-10-23): expired `AuthSession` rows are never vacuumed

`_session_from_request` checks expiry but nothing deletes expired rows; minor DB growth over time. (No password-change endpoint exists, so revocation-on-password-change is moot today.)
**Status (2026-10-23):** vacuumed on each successful login — one indexed range delete
(`expires_at <= now`), logins being infrequent enough to bound the table without the
scheduler this repo deliberately avoids (`api/routes/auth.py:155-163`). A session-token
leak therefore still needs the per-class rotation notes in the tracker, not a scheduler.
Test: `tests/test_api_key_gate.py::test_successful_login_vacuums_expired_sessions` (expired
row gone, live row untouched). Still open from the same note: there is no password-change
endpoint, so revocation-on-password-change remains unwired.

### F13 — ✅ RESOLVED (narrow CSP, 2026-10-23): no security headers / no CSP on the served UI

The UI is served with only `Cache-Control: no-cache`; no CSP (the inline `SOC_CONFIG` bootstrap would need a nonce), no `X-Content-Type-Options`, and authenticated JSON responses lack `Cache-Control: no-store` (browsers may cache case data). Minor at current exposure; worth a pass before any wider rollout.
**Status (2026-10-23):** `_request_id` stamps `X-Content-Type-Options: nosniff` on every
response and `Cache-Control: no-store` on `/api/*` only, so static assets keep their caching
while case data never lands in the browser cache (`api/middleware.py:96-113`). It also emits
this CSP on every response:

```
object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'
```

Those four directives constrain what an injected document could do (no plugins, no `<base>`
hijack, no framing/clickjacking, no cross-origin form posts) and **cannot** affect loading.
That is deliberate: this UI is the Vue **global** build compiling `template:` strings at
runtime plus the Tailwind **Play CDN**, and `SOC_CONFIG` is an inline `<script>` — a
`script-src`/`style-src`/`default-src` policy would have to allow `'unsafe-eval'` and
`'unsafe-inline'` (or carry a nonce plus a precompiled build) to keep the page working, which
would make the restrictive part decorative. A strict CSP is a build-system change, not a
header change; until then the four directives are the honest maximum. Pinned by
`tests/test_api_key_gate.py::test_security_headers_on_api_and_ui`, which also asserts no
`script-src`/`default-src` appears — so adding one forces a deliberate test change.

### F14 — LATENT (unmounted): attic endpoints carry no admission checks at all — reactivation checklist

Audited every endpoint-bearing file in `scripts/attic/` (`closure_note_endpoint.py`, `fix_closure_endpoint.py`, `db_routes.py`, `update_api.py`; plus patchers `apply_closure_fix.py`, `fix_endpoint.py`, `update_closure_endpoint.py` that carry endpoint source as string literals). **None carries an inline admission check** — the only non-validation statuses are two 503 Ollama-availability probes in `db_routes.py:110,133` (dependency checks, not gates), and the inline checks that do exist are input validation (400 `rule_id and case_id required`), existence (404), template `KeyError` (400), and pydantic `Query(ge=, le=)` bounds. So reactivation cannot drift from `api/helpers/admission.py`'s boundary semantics with a competing inline check — the risk is the inverse: attic endpoints predate the gates and would **skip them entirely**:

- **No admission** — attic `/api/db/analyze` blocks on Ollama with no queue gate (live: `admission.queue_rejection`, `api/routes/tools.py:311`) and no wall-clock cap (live: `ANALYZE_TIMEOUT_S` 300 s → 504 in `api/routes/analyze.py`); closure-note accepts an unbounded raw `dict` body with no payload byte budget (live: `admission.payload_rejection`, `api/routes/notables.py:1315`); nothing consults the Splunk mode latch (`api/routes/splunk.py:62`) or the per-resource concurrency cap (`api/routes/splunk.py:125`).
- **No auth/CSRF** — attic endpoints declare no `require_api_key`/`require_role`/CSRF dependencies (`db_routes.py` depends only on `get_db`); they predate the auth scaffold (`api/auth.py`).
- **F1 regression vector** — `update_api.py` rewrites `api/main.py` including the **old** global handler `{"detail": str(exc)}` — the exact pattern F1's fix (293b366) eliminated. Re-running that patcher silently reintroduces raw exception text in 500 bodies; its `/api/db/rules` also swallows every exception to `[]`.

**Action if any attic endpoint is ever reactivated:** wire it through `api/helpers/admission.py` (measure inputs, raise the returned exception), attach the same auth/CSRF dependencies as its live siblings, keep the generic-500/`raise_internal` pattern, and never re-import the pre-F1 exception handler. The attic is currently unmounted — no module in `api/` imports it (verified by static search) — so this is latent, not live, exposure.

### F15 — ✅ RESOLVED (found and fixed 2026-10-23): CORS preflight rejects the `X-CSRF-Token` header

Session mode requires `X-CSRF-Token` on every mutating `/api/*` request, and the UI stamps it
on every axios call (`web/utils/auth.js:46`). The CORS middleware admits exactly
`["Content-Type", "Accept", "X-API-Key"]` (`api/middleware.py:298`) — `X-CSRF-Token` is not
on the list, so a **cross-origin** browser client cannot pass the preflight that protects the
header. Same-origin UI (served by the API itself on :8000) is unaffected, which is why this has
never surfaced. *(Original finding text kept above; the header is now allowlisted — see the
fix note at the end of this entry.)*

**Live probe (2026-10-23, real app via `TestClient`, `CORS_ORIGINS` set):**
```
OPTIONS /api/auth/login
  Origin: https://ui.example.test
  Access-Control-Request-Method: POST
  Access-Control-Request-Headers: content-type,x-csrf-token
→ 400
  Access-Control-Allow-Headers: Accept, Accept-Language, Content-Language, Content-Type, X-API-Key
```

**Impact:** availability/interop, not exposure — the failure is closed (the mutation is
refused, never waved through). It bites exactly the deployment shape this middleware's own
docstring describes ("the hosted branch preview can call a locally running API through a
secure HTTPS tunnel"): a cross-origin session-mode UI can read (GETs need no CSRF) but cannot
mutate anything. API-key-only clients are unaffected.

**Fix (landed 2026-10-23):** `"X-CSRF-Token"` added to `allow_headers`
(`api/middleware.py:310`). The same probe now answers **200** with
`Accept, Accept-Language, Content-Language, Content-Type, X-API-Key, X-CSRF-Token`, while a
header not on the list still answers **400** — the allowlist did not become a wildcard. Both
halves are pinned: `tests/test_api_key_gate.py::test_cors_allows_x_csrf_token_header` and
`::test_cors_preflight_still_refuses_unlisted_headers`, so a future header added to
`web/utils/auth.js` cannot repeat this silently.

## Optimizations

1. **Cache actor resolution** — `resolve_actor` → `_session_from_request` runs 2 DB queries (AuthSession + User) on **every** `/api/*` request in session mode (`api/auth.py:167-186`). A short-TTL (30–60 s) in-memory `token_hash → (user_id, role, csrf)` cache, invalidated on logout, removes most of that load; the UI polls frequently. **Landed:** `ACTOR_CACHE_TTL` (env, default 30 s, `0` disables) — a per-process `token_hash → CachedUser` map holding plain values (no ORM rows attached to the request); `destroy_session` evicts the entry on logout, closing the race where gate middleware caches the actor moments before logout. CSRF stays per-request (double-submit unaffected). Bounded staleness: a role change or deactivation applies within one TTL. Regression tests: `tests/test_api_key_gate.py::test_first_resolution_pays_db_and_warms_cache`, `::test_cache_hits_spare_db_queries`, `::test_cached_session_expiry_drops_entry`, `::test_logout_evicts_cached_actor`, `::test_actor_cache_disabled_when_ttl_zero`, `::test_cache_hit_evaluates_csrf_per_request`, `::test_cache_serves_cached_user_shape`.
2. **Cache the bootstrapped index HTML** — `/index.modular.html` re-reads the file and re-injects the config on every page load (`api/main.py:271-294`). Cache keyed on `(_API_KEY, mtime)`. **Landed:** the rendered page is cached on `(_API_KEY, mtime)` — a page load is one `os.stat` plus a dict lookup; the file is re-read and `window.SOC_CONFIG` re-injected only when the key changes; 404-on-missing preserved. Regression tests: `tests/test_api_analyze_flow.py::test_index_html_cached_across_repeat_loads` (repeat loads read the file once via patched `open`), `::test_index_html_cache_invalidated_on_file_change`, `::test_index_html_cache_keyed_on_api_key`, `::test_index_html_cached_verbatim_when_disarmed`.
3. **Bound concurrent tool executions separately from the queue cap** — up to 100 RUNNING jobs each block an anyio threadpool thread (default ~40 tokens) for up to 300 s; beyond ~40 concurrent runs, sync endpoints starve. A running-semaphore (e.g. 4–8) keeps the queue at 100 but caps simultaneous subprocesses. **Landed:** `TOOL_CONCURRENCY_MAX` (env, default 6) bounds simultaneous subprocesses in `api/routes/tools.py`; `execute_tool_async` is now a coroutine, so jobs waiting for a permit park on the event loop (zero anyio threadpool tokens) and only the subprocess run itself — under a permit — occupies one. Permit pools are cached per event loop (`_tool_run_semaphores`) because asyncio primitives bind to their loop; the job queue stays at `JOB_QUEUE_MAX`=100. A job deleted while parked is re-checked once a permit is acquired, so a cancelled job never starts its subprocess. Regression tests: `tests/test_api_key_gate.py::test_tool_execution_concurrency_capped_by_semaphore` (end-to-end: 5 concurrent requests with the cap at 2 never run more than 2 subprocesses at once, all jobs complete), `::test_parked_tool_jobs_hold_no_threadpool_tokens` (the starvation fix: 4 live jobs with the cap at 2 hold exactly 2 of the ~40 threadpool tokens), `::test_deleted_while_waiting_job_never_starts_subprocess`, `::test_tool_run_semaphore_pool_is_per_event_loop`.
4. **Cheaper admission count** — `_unfinished_job_count()` fetches every `ToolRun` row on each `/api/execute` (`api/routes/tools.py:55-77`); a `func.count()` filtered on status suffices at scale. **Landed:** the persisted tally is a single `func.count(ToolRun.job_id)` filtered on unfinished statuses — legacy `"JobStatus.X"` persisted strings are normalized in SQL (`lower(trim(replace(status, 'JobStatus.', '')))`, same semantics as the old Python-side normalization; NULL statuses fall out of the `IN` and are excluded) — and `job_id NOT IN` the live unfinished set keeps the both-stores dedup. No row fetch. Regression test: `tests/test_api_key_gate.py::test_unfinished_job_count_uses_db_side_count` (captures executed SQL via an event listener: mixed live+persisted tally, legacy status strings counted, finished rows excluded, live-dict dedup, and every `tool_runs` SELECT is an aggregate).
5. **Narrow `_tool_artifact_snapshot()`** — it walks 5 directory trees before *and* after every tool run (`api/routes/tools.py:109-131`); scoping to registry-declared output dirs makes small-tool runs cheaper. **Landed (2026-10-05):** the registry now carries per-tool `output_dirs` — tools declare output locations with `# OUTPUT_DIR:` headers, parsed by the indexer (separator-normalized, `../`-refusing) into the entry, and 11 verified writer tools are seeded. `_tool_artifact_snapshot(tool_name)` walks only that tool's declared dirs when declared (realpath containment: `..`-escapes and outside-root absolutes refused, the #15 traversal stance), keeping the **full legacy 5-tree sweep as fallback** for undeclared tools or empty resolutions — narrowing is opt-in per tool and artifact detection never silently shrinks. The 464-file `Active_Workspace` walk now happens only for tools that actually write there. Regression tests: `tests/test_api_key_gate.py::test_declared_tool_snapshot_walks_only_its_output_dirs`, `::test_undeclared_tool_keeps_legacy_sweep` (incl. registry-failure degradation), `::test_declared_tool_artifacts_detected_end_to_end`, `::test_output_dir_traversal_and_abs_outside_root_refused`, `::test_indexer_output_dir_header_round_trip`.
6. **Frontend error rendering** — verify the UI renders `detail` via `textContent`, not `innerHTML`; with F1's `str(exc)` in the payload, innerHTML rendering would turn server errors into an XSS vector. **Landed (audit + canary, 2026-10-05):** audited all 26 web/ JS/HTML sources — **zero** raw HTML-injection sinks (`innerHTML`/`insertAdjacentHTML`/`outerHTML`/`document.write`/`eval`/`new Function`/`srcdoc`/`javascript:`); error `detail` flows only through `alert()`, `console.error`, and Vue `{{ }}` interpolation (auto-escaped) — the #6 check **passes with no fix needed**. One `v-html` exists repo-wide (AnalysisTab.js Phase-2 analysis panel ← `formatAnalysisText`): audited safe (escape-before-format; escaper covers `& < > "` and generated tags/attributes are static double-quoted), hardened anyway by adding `'` → `&#39;`. Pinned with `tests/test_frontend_xss_sinks.py` (3 tests: sink scan across web/, v-html confined to AnalysisTab.js, escaper completeness + & -first order) — the first frontend regression coverage, running in CI pytest on every push.

## Already tracked (not new)

Path-traversal fix (#15), docs gating (#14), CORS tightening (#7), auth scaffold + activation (#5/#6), dependency audit (#11), Splunk boundary latch, keychain secret storage (#3) — all in `docs/security-remediation-tracker.md`.
