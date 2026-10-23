# Backlog — the single live list

*Created 2026-10-23. This file replaces the punch lists and punch-list-style
paperwork that were archived the same day (`docs/archive/`). Everything here is
open work; shipped work lives in `docs/RELEASE_NOTES.md` and git history.*

**How to use it:** take the top item you are allowed to start, do it, delete the
line (or move it to `docs/RELEASE_NOTES.md`). Priorities are about *risk and
blocking*, not excitement. Re-derive any number before trusting it
(`docs/CURRENT_STATE_CHECKS.md`).

Current baseline: **529 tests**, 3 UI harnesses, 74 endpoints across 14 routers,
`dev-dalton` at `4437c0e` and in sync with origin, CI green, baseline holding,
31 dirty paths — all of them another session's in-flight Splunk/SBOM work.

---

## 📋 Work order 2026-10-23 — agentic maintenance batch

Assigned by the owner (email, 2026-10-23 14:27). **These are the active items**;
they outrank the standing P0/P1/P2 list below, which remains the engineering
backlog.

**Every task must deliver:** id + objective · files inspected/changed ·
implementation summary · unified diff · tests added · commands run + results ·
known limitations and regression risks · assumptions · human-review questions ·
explicit confirmation that no secrets, production data, credentials or
live-system actions were used.

**Standing rules for this batch (binding):**

- This repo, branch and disposable working copy only; synthetic or approved
  non-production data. The **local** `soc_platform` DB qualifies; the shared Neon
  mirror does not.
- Minimum change; preserve existing behavior unless the task requires a change.
- **No commit, push, PR, deploy, `kubectl apply` or CI/CD edit** without
  per-task authorization from the owner. (The local-first batch was authorized;
  these are not.)
- No secrets, tokens, certificates, keys, real user records or production values
  in code, tests, logs or chat.
- Never weaken auth, authz, logging, rate limiting or secret scanning to make a
  check pass.
- No invented cluster values: namespaces, labels, ports, storage classes, subnet
  ranges and header names arrive as placeholders.
- **Stop and ask** for: production values, the sensitive-column list,
  key-management decisions, role policy, rate-limit thresholds, and any route
  removal with external-client impact.

### Phase 1 — bug fixes and workflow

| ID | Task | Status / evidence |
|---|---|---|
| **DEV-01** | Pre-push scanner must use the active venv | ✅ **Landed + pushed** in `d9252a8`: the hook prefers `.venv314` → `.venv` → `python3`, which is what made the gate pass. **Open sub-item:** the order wants a hard failure when no approved venv exists rather than a `python3` fallback — a policy call, because it would block pushes from any clone without a venv (CI unaffected). |
| **BUG-01** | Hostname regex over-redacts hyphenated tags and ISO-8601 | ✅ **Fixed + pushed** `279d5c0` (2026-10-23) — 21 regression cases, negative control reproduced 8 failures pre-fix. Was confirmed real, single site: `Tools/text_sanitizer_pipeline/text_sanitizer_pipeline.py:139` — `\b([A-Za-z0-9]+[-][A-Za-z0-9-]+)\b` matches any two alnum runs joined by a hyphen, so `2026-10-23T18` is redacted out of `2026-10-23T18:28:43` and tags like `win-update-rollup` become hosts. Narrow to multi-label hostnames, exclude timestamp shapes first, keep `host=`/`dest=` context intact. Tests belong in `tests/test_investigation_fixes.py` (already covers the sanitizer; no new test file). |
| **BUG-02** | Catalog fuzzy-matcher `raw_fields` fallback | ✅ **Fixed + pushed** `30ffefa` (2026-10-23) — 5 regression cases, negative control reproduced 2 failures pre-fix. Was confirmed real and pinpointed: `api/routes/analyze.py:220-232` builds its anchor from `case.rule_name` plus `(source_notable_payload or {}).get("fields")` — there is no `raw_fields` path, while the paste/store path writes `raw_fields` (`api/routes/notables.py:1378`). A notable whose fields live only under `raw_fields`, or whose `rule_name` was sanitized away, scores 0-1 tokens and silently generates no cards. Fix: fall back `fields = payload.get("raw_fields") or payload.get("fields") or {}` and fall back the rule name from `raw_fields` (`rule_name`/`search_name`) before matching. Tests: `tests/test_api_analyze_flow.py` (`TestPlaybookFamilyGuard` already exercises this code). |

### Phase 2 — security and backend hardening

| ID | Task | Blocker / note |
|---|---|---|
| **SEC-01** | `AUTH_MODE=proxy` header authentication | **Needs human input:** header name, the numeric-local-user-id validation rule, and the trusted-proxy boundary. Current modes are API key + session (`api/auth.py`, `api/middleware.py`); the new mode must not alter them. Tests: valid, missing, malformed, unmapped user, disabled mode, existing modes. |
| **SEC-02** | Read-vs-write role separation on every route | 74 endpoints across 14 routers; RBAC currently covers 9 (`API_SECURITY_REVIEW` F4). Deliverable includes the route/role matrix; expanding the role policy is an owner decision. |
| **SEC-03** | Column-level encryption (`TypeDecorator`) | **Blocked pending the approved column list** and key-management decisions (injection, rotation, backup/restore, indexing/searchability). Do not guess columns; test-only generated keys. |
| **SEC-04** | Rate-limiter coverage | Extends F8 (keying is per-IP only; coverage is Ollama-backed POSTs). Thresholds only if supplied — otherwise reuse the existing 30/min and say so. |
| **SEC-05** | Remove or gate unmounted routes | Inventory first: `api/main.py` mounts 12 routers (11 feature routers + auth); `scripts/attic/` holds retired handlers; `API_SECURITY_REVIEW` F14 flags attic endpoints as latent. Removal needs confirmation that no external client depends on them. |
| **SEC-06** | Disable production source maps | **Probably N/A — needs a decision.** There is no bundler: `web/` is CDN React plus plain JS, no `package.json`, no build step, so no source maps exist to disable. Confirm N/A or define what "production build" means here before changing anything. |

### Phase 3 — Kubernetes drafts (local validation only, never applied)

Existing manifest: `k8s/soc-api-deployment.yaml` (API + fluent-bit sidecar,
invariants pinned by `tests/test_k8s_manifest.py`). New work is additive drafts
with placeholders, validated locally (`kubectl --dry-run=client` / schema tooling
if present — neither is installed on this Mac today, so that check may be
unavailable and must be reported as such rather than claimed).

| ID | Task | Placeholders required |
|---|---|---|
| **K8S-01** | Ingress-restriction NetworkPolicy for the API pod | ingress namespace + labels, app port |
| **K8S-02** | Zero-egress NetworkPolicy for the local-inference pod | API pod selector + ports; explicit decisions for DNS, metrics, logging, image pulls |
| **K8S-03** | NGINX ingress header sanitization | header names, trusted-proxy config; pairs with SEC-01 |
| **K8S-04** | Postgres 15 StatefulSet + encrypted StorageClass | storage class, size, secret name, image digest |

### DEV-02 — workspace inventory (phase 1, recurring)

Run when the tree is dirty. Categorize every path as: intentional change,
dependency/lockfile update, test fixture/mock data, generated artifact,
temporary local artifact, or unknown-needs-human. Do not delete or commit
anything without authorization. **Snapshot 2026-10-23:** 31 paths, all from the
concurrent Splunk/fake-server session — `services/splunk_*`,
`scripts/fake_splunk.py` + `seed_demo_corpus.py`, `docs/FAKE_SPLUNK_DEMO.md`,
`scripts/check_sbom_pins.py`, `docs/SBOM.json` + audit docs, the demo-corpus
seeds and two tests, plus their hunks in `scripts/git-hooks/pre-push` and
`docs/CURRENT_STATE_CHECKS.md` (mine are committed, theirs are not). No secrets
or production values; `services/mock_splunk/seed/*` is synthetic corpus.

---

## 🔴 P0 — standing list (item 1 done 2026-10-23)

### 1. ~~Land the local-first database change~~ ✅ **done**
Pushed as `f3976ba` (db core) → `d9252a8` (launchers, mirror, tests, baseline) →
`4437c0e` (docs, archive sweep, backlog). CI run `40003601261` green, pre-push
gate 8/8 security probes. Other sessions' files were left untouched and
uncommitted.

### 2. Prove a restore (the backup has never been restored)
Dumps exist (`local-backups/`, 13 tables each, 20h cadence riding on
`scripts/start`), and `docs/BACKUP_AND_RESTORE.md` has a procedure — but no
restore has ever been executed. A backup you have not restored is a hope.

**Steps:** `createdb soc_platform_restore_verify` → `gunzip -c <newest> | psql`
→ compare `triage_results`, `splunk_events`, `supportive_query_results`,
`users`, `analysis_results` counts against the live DB → `dropdb`.
**Then** write the witnessed result (counts + date) into
`docs/BACKUP_AND_RESTORE.md` and add a `scripts/restore_verify` one-shot if the
manual steps are more than five commands.

**Acceptance:** restore of the newest dump into a scratch DB reproduces every row
count; scratch DB dropped; result recorded in the doc.

### 3. Make `/api/health` tell the truth
`api/routes/system.py` returns a static `{"status": "healthy"}` — **no dependency
is checked**. It is the launcher's readiness probe *and* the deploy smoke, so it
reports healthy with Postgres down (the launcher's own DB ping happens earlier,
which is the only reason nobody has been burned yet).

**Change:** add a bounded (≤1s) `SELECT 1` plus a cheap table count; return
`503 {"status": "degraded", "db": "down" | "error: …"}`. Keep it unauthenticated
and allocation-light. Do **not** put Ollama in the failure path (the platform
runs without a model) — report it as an advisory field.

**Acceptance:** hermetic tests (fake session that raises → 503; healthy → 200);
`scripts/start` still reports healthy; `scripts/start --check` stays read-only.

---

## 🟠 P1 — next

### 4. Live Splunk rehearsal (blocked on credentials — ask Justin)
`RealSplunkBackend` is contract-tested and now de-risked by
`scripts/fake_splunk.py` + `docs/FAKE_SPLUNK_DEMO.md` (real HTTP, TLS, failure
knobs), but it has **never touched a licensed instance**. Needs `SPLUNK_URL` +
a read-only `SPLUNK_TOKEN` into BWS/keychain, then
`docs/SPLUNK_REHEARSAL.md` end to end. This is the last gap between "the loop
works against fixtures" and "the platform is useful on real data".

**Acceptance:** rehearsal checklist complete, CSV normalization verified on real
rows, failure drill (403/503/timeout) reproduced against the real instance.

### 5. Make the shared mirror actually pullable
`scripts/mirror_db pull` stops at the version guard: Neon is PG 18, this Mac has
only the PG 16 client. **`brew install postgresql@18`** (client only; do not
start a second server) then
`PGDMP=/opt/homebrew/opt/postgresql@18/bin/pg_dump scripts/mirror_db pull`.
Then run the real snapshot → scratch DB → compare counts, and document the
"hand Justin a snapshot" path (Shared folder / tailnet, never chat).

**Acceptance:** one shared snapshot in `local-backups/`, loaded and verified in a
scratch DB, `push` still refused.

### 6. Retention proven at scale
`keep_last_n=20` with closure-protected cases is implemented and unit-tested,
but the live dry run now says `scanned=0` — local has no analysis results.
Seed ~30 `analysis_results` for a disposable case (one with a closure note),
run `scripts/prune_analysis_results.py` dry, then `RETENTION_APPLY=1 … --apply`,
and confirm exactly the expected rows went and the closure-linked case survived.

**Acceptance:** apply-run output recorded; protected rows intact; delete the
seeded case afterwards.

### 7. Log rotation and a disk guard
`/tmp/soc-api.log` and `/tmp/ollama.log` are unbounded (22K today, but an
analysis-heavy week writes a lot; the boundary/audit path can be loud too).
No scheduler exists by decision, so rotate **at launch**: if a log exceeds
`SOC_LOG_MAX_BYTES` (default 5 MiB), move it to `.1` and start fresh. Add the
log sizes to `scripts/start --check`.

**Acceptance:** forcing a small cap rotates on the next start; `--check` reports
sizes; nothing else changes behavior.

### 8. One diagnostic command (`scripts/doctor`)
Today the "am I OK?" answer is spread across `scripts/next`,
`scripts/start --check`, `db.target --check`, and `scripts/baseline --verify`.
One read-only command that prints a table — API, DB target + ping, backup age,
log sizes, keychain secrets present (names only), schema drift, mirror client
version, git branch/dirty — would make the AGENTS.md "first 5 commands" one
command.

**Acceptance:** `scripts/doctor` exits 0 healthy / 1 with a named failure; no
side effects; covered by a shell-level test in `tests/test_db_target.py` (or the
playbook's gate script) for the exit-code contract.

---

## 🟡 P2 — when the above is done

### 9. Browser-level end-to-end smoke
The 3 node-vm harnesses drive real components but no browser, no cookies, no
CSRF, no login. Highest-value regression net for a 9.4k-LOC UI:
login → paste notable → promote → run mock query → save evidence → **reload** →
evidence hydrates. Playwright (or the preview/CDP tooling) against
`127.0.0.1:8000` with a disposable DB.

### 10. Security remainder
- **F8:** expand rate limiting beyond Ollama-backed POSTs and key it on actor
  (session user) as well as IP.
- **F4:** RBAC covers 9 endpoints; either widen or record the exact accepted-risk
  surface in `docs/API_SECURITY_REVIEW.md` (it is currently "DECIDED", not
  enumerated per route).
- Session UX: expiry/renewal behavior in the UI (today an expired session looks
  like a failed action).

### 11. Second-machine story without Neon
The mirror is one-way and manual; Justin's path is currently "shared Neon or
nothing". Decide and document: tailscale-only SFTP of an encrypted snapshot vs.
`--shared` for live collaboration. Includes the **Tailscale licensing question**
(Personal plan is homelab/personal — this is work use) and the Windows compose
story (`docs/SETUP.md`, `CONTAINERIZATION.md`).

### 12. Documentation debt
- `docs/EXECUTION_PLAYBOOK.md` (917 lines) documents stages A–E, **all complete**,
  and carries stale counts. Keep its standing rules/guardrails/gates as a short
  `docs/WORKING_RULES.md`, archive the rest.
- `docs/DEVELOPMENT_PLAN.md` §1 still says the runtime is Neon and the suite is
  276 tests on 3.14 + 3.9; §11's "recommended order" is historical.
- `docs/RELEASE_NOTES.md` counts (304 × 2, 8 test files) and
  `docs/EXECUTION_PLAYBOOK.md`'s "expect: 8" are stale vs. disk (17 files, 502
  tests).
- No `LICENSE` in a public repo (owner decision).

---

## Human-blocked / not code

| Item | Owner | Note |
|---|---|---|
| Splunk read-only token | Justin | unblocks #4 |
| Tailscale plan fit for work use | Dalton | #11 |
| Dependabot PR review | Dalton | watch the `<3.10` accepted-risk pins |
| Safari history scrub / thread deletion | Dalton | D3, unchanged |
| `LICENSE` choice | Dalton | public repo |

## Done, for the record (Oct 23)

- Local-first database cutover (code + docs + tests), Neon demoted to mirror.
- Pre-push gate interpreter fix: bare `python3` is 3.9.6 on this Mac, so both
  gates were evaluating the marker-dead `<3.10` branch and failing against a
  3.14-shaped SBOM — a guaranteed false "push BLOCKED". Venvs are now preferred;
  the gate passes end to end.
- Paperwork sweep: 6 stale documents archived with an index; cheat sheet and
  backup doc corrected where they stated false things.
