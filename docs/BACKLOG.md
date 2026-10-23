# Backlog — the single live list

*Created 2026-10-23. This file replaces the punch lists and punch-list-style
paperwork that were archived the same day (`docs/archive/`). Everything here is
open work; shipped work lives in `docs/RELEASE_NOTES.md` and git history.*

**How to use it:** take the top item you are allowed to start, do it, delete the
line (or move it to `docs/RELEASE_NOTES.md`). Priorities are about *risk and
blocking*, not excitement. Re-derive any number before trusting it
(`docs/CURRENT_STATE_CHECKS.md`).

Current baseline: **502 tests**, 3 UI harnesses, 74 endpoints across 14 routers,
`dev-dalton` in sync with origin, baseline holding, 58 dirty paths (mostly item 1
plus another session's in-flight work).

---

## 🔴 P0 — do now (unblocks everything else)

### 1. Land the local-first database change
Local Postgres is the runtime primary and Neon is an opt-in mirror — **verified
working, entirely uncommitted** (~57 dirty paths, mixed with another session's
work). Until this is committed and pushed it is one `git checkout` from gone, and
`DEVELOPMENT_PLAN` §1.17's collaboration hazard (Justin replaces history with
fresh snapshots) makes unpushed work genuinely at risk.

Split into three commits so review is possible:
1. `db/target.py`, `db/schema.py`, `db/models.py`, `scripts/lib/db_target.sh`,
   `scripts/dev`, `scripts/start` — resolution + launchers.
2. `scripts/mirror_db`, `docs/LOCAL_FIRST_DATABASE.md`, `.env.example`,
   `AGENTS.md`, `SETUP.md`, `README.md`, `OPERATOR_CHEAT_SHEET.md`,
   `docs/BACKUP_AND_RESTORE.md`, `docs/CURRENT_STATE_CHECKS.md`, `.baseline.json`.
3. `tests/test_db_target.py` (+ count bump in `CURRENT_STATE_CHECKS.md`).

**Do not** stage another session's files (`api/middleware.py`,
`services/splunk_*`, `docs/SBOM.json`, `scripts/check_sbom_pins.py`, the
demo-corpus files, …).

**Acceptance:** `git push` succeeds — the pre-push gate is green end to end
(baseline, markers, SBOM, compile, hygiene, web keys, 8/8 live security probes;
verified 2026-10-23 after fixing the gate's interpreter order).

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
