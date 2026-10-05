# Live Splunk Rehearsal (D2) — Turnkey Runbook

*Assembled Sept 30, 2026 (execution-playbook stage D2). Upgraded to full turnkey
Oct 5, 2026 — every command and expectation in here was checked against the live
code (`api/routes/splunk.py`, `services/search_backend.py`,
`scripts/secrets-keychain`, `tests/test_api_analyze_flow.py`) before being
written down. Blocked only on credentials: once `SPLUNK_URL` + `SPLUNK_TOKEN`
exist, this runs start-to-finish in ~20 minutes, with nothing left to decide.*

**Secrets rule:** `SPLUNK_TOKEN` is a credential. It travels by BWS vault,
keychain injection, or a call — never pasted into chat, docs, or commits. Every
verification step below prints *names only*.

**Scope guard:** the connector is read-only by design — one round-trip
`POST {SPLUNK_URL}/services/search/jobs/export` (bearer auth). No job
management, no index writes, no ES notable updates. There is nothing to undo on
the Splunk side, ever.

---

## 0. Prerequisites (5 min, no credentials needed)

- [ ] Platform healthy: `bash scripts/start --check` → API up on `127.0.0.1:8000`.
- [ ] A case with a **source notable** exists (search-one 404s without one — it
      substitutes placeholders from the notable). List cases:
      `curl -s http://localhost:8000/api/db/triage | python3 -m json.tool | head -40`
      If empty: `.venv314/bin/python scripts/seed_mock_splunk.py` seeds the three
      demo scenarios (marked cases, safe on the shared Neon DB — they are the
      sanctioned demo corpus; do **not** create ad-hoc test cases).
- [ ] Note one demo `case_id` for reuse below.
- [ ] Real Splunk reachable from this Mac over HTTPS on its management port
      (default `8089`).

**Armed-gate variant:** if the API key gate is armed (`API_KEY` set), the UI
needs nothing (the key is server-injected into `window.SOC_CONFIG`), but every
`curl` below must add: `-H "X-API-Key: $(scripts/secrets-keychain get API_KEY)"`.
Examples below omit it; add it if `/api/health`-authenticated routes 401.

## 1. Land the credentials (BWS → .env → keychain)

1. **BWS UI** (you or whoever holds the vault): org **SSC-Lewis** → project
   **SOC Platform** → add three secrets to the machine account's scope:

   | Key | Value | Secret? |
   |---|---|---|
   | `SEARCH_BACKEND` | `splunk` | no (config) |
   | `SPLUNK_URL` | `https://splunk.example.com:8089` (base URL, no path) | no |
   | `SPLUNK_TOKEN` | the read-only bearer token (`search` capability only) | **yes** |

2. **Pull and migrate:**
   ```bash
   scripts/pull-secrets          # regenerates .env from vault, then auto-migrates
   scripts/secrets-keychain list # expect:  SPLUNK_TOKEN  ok
   grep -c '@keychain' .env      # expect:  >= 5 (DATABASE_URL, API_KEY, SPLUNK_TOKEN, …)
   ```
   `SPLUNK_TOKEN` is in `scripts/secrets-keychain`'s `KEYCHAIN_KEYS`, so
   `pull-secrets`'s migrate step moves it out of plaintext `.env` automatically
   and leaves a `# SPLUNK_TOKEN=@keychain` marker. Verify the marker, never the
   value: `grep 'SPLUNK_TOKEN' .env` must print only the marker comment.

   *Manual fallback* (if BWS can't hold it yet): `scripts/secrets-keychain put
   SPLUNK_TOKEN` (value on stdin, never echoed), then export
   `SEARCH_BACKEND`/`SPLUNK_URL` in the shell that launches `scripts/start`.

3. **Read-only token check** (Splunk admin confirms): the account has the
   `search` capability only — no `admin`, no `edit_saved_searches`, no index
   writes. Rehearsal probes must all be plain searches.

## 2. Restart and arm

```bash
bash scripts/start            # bws run injects vault secrets; keychain fills the rest
bash scripts/start --check    # strictly read-only status
curl -s http://localhost:8000/api/health
```

**Expected:** startup completes. (Unconfigured `SEARCH_BACKEND=splunk` fails
loudly at construction — `SplunkSearchError`, "requires SPLUNK_URL +
SPLUNK_TOKEN" — so a silent mock fallback is not possible.)

## 3. Probe A — arbitrary-SPL path (proves the connector, no platform data needed)

The payload's explicit `spl` override bypasses stored templates, so this works
even though the mock seed corpus doesn't exist in real Splunk. The distinctive
`query_title` scopes the re-run-replaces rule to exactly this probe and makes
cleanup trivial.

```bash
curl -s -X POST http://localhost:8000/api/splunk/search-one \
  -H 'Content-Type: application/json' \
  -d '{
        "case_id": "<DEMO_CASE_ID>",
        "query_title": "LIVE REHEARSAL PROBE",
        "spl": "| makeresults count=3 | eval probe=\"live-rehearsal\""
      }' | python3 -m json.tool
```

**Expected:** HTTP 200 with `"backend": "RealSplunkBackend"`,
`"result_status": "success"`, `"row_count": 3`, three rows each carrying a
`timestamp` field. (No `$placeholders$` in this SPL → no 422 path.)

## 4. Probe B — template path (proves the full analyst loop)

Open the analysis wizard on the demo case, pick a Stage-2 supportive-query
card, press **Run in Splunk** (or **Run All**). The endpoint resolves the
stored SPL template, substitutes `$host$`/`$user$`/… from the case's notable
fields, and executes.

**Expected:** the card flips to `Saved ✓`; the Evidence Timeline gains a
`search_one / splunk_auto` row for that query title; a re-run **replaces** the
prior auto row for that (case, query title) rather than duplicating it.

**Pass criteria for an empty result:** if the real index has no matching data,
`"result_status": "no_results"` with row_count 0 is a **pass** — outcome
mapping is part of the contract (`map_search_outcome`: 0 rows ⇒ `no_results`,
error ⇒ `query_failed`, else `success`). An unresolved placeholder ⇒ HTTP 422
listing the tokens — also a pass if it names the missing notable fields.

## 5. Verify the connector contract (one block, after Probes A/B)

| Behavior | How | Expected |
|---|---|---|
| **CSV normalization** | Open the probe row in the Evidence Timeline; compare its `timestamp` against the event time in Splunk | `_time` epoch → naive-UTC ISO string, correct instant |
| **Stats-row shape** | Re-run Probe A with `"spl": "index=* \| stats count by sourcetype \| head 5"` | Saves fine with no `_raw`; `count`/`sourcetype` fields present, missing keys defaulted to `""` |
| **`search` prefixing** | `curl -s http://localhost:8000/api/jobs/<job_id>` and read `arguments.spl` | Bare terms got `search ` prepended; `\| makeresults`/`\| stats`/`rest`/`savedsearch` did not |
| **Bearer on the wire** | In Splunk: `index=_audit sourcetype=audittrail` filtered to your user | `POST /services/search/jobs/export` → HTTP 200, bearer auth |
| **Boundary-latch interplay** | `curl -s http://localhost:8000/api/splunk-boundary/status` | `"mode": "quarantined"` **while probes succeeded** — the latch gates HTTP *admission* only (code: only the admit endpoints check `current_mode()`); read-only search-one never consults it. Do **not** flip the latch for this rehearsal |
| **Concurrency guard** | Fire two Probe A POSTs back-to-back for the same case | Second returns HTTP 409 ("per-case concurrency cap is 1"); no duplicate ledger row |
| **Audit trail** | `curl -s http://localhost:8000/api/jobs/<job_id>` | `tool_runs` row: `tool_name=splunk_search_one`, `status=completed`, `exit_code=0`, `arguments.spl` = exact executed SPL |
| **Timeout guardrail** | Optional: `export SEARCH_ONE_TIMEOUT_SECONDS=5`, restart, run a long real search | `result_status=query_failed`, `error` starts with "Search timed out after 5s"; card inline status `Run failed` |

## 6. Failure drill (bad token)

```bash
export SPLUNK_TOKEN=bad       # in the launching shell, BEFORE scripts/start
bash scripts/start            # explicit env wins over keychain for this launch
# re-run Probe A
```

**Expected:** HTTP 200 with `"result_status": "query_failed"` and `error`
starting `"Splunk export failed with HTTP 401"`; the UI card shows **Run
failed** inline, no crash. **The ledger row IS written**: the (case,
"LIVE REHEARSAL PROBE") `splunk_auto` row is replaced with one whose
`result_status=query_failed` — this is pinned by
`TestSearchOneMidLoop::test_unsupported_spl_maps_to_query_failed`
(`raw_result.result_status == "query_failed"`), which is why the probe title is
distinctive: the failed row overwrites only the probe's own prior row. The
`tool_runs` row shows `status=failed`, `exit_code=-1`.

Then restore: unset the override (or fix the token), restart, re-run Probe A →
the success row **replaces** the failed row (same replacement rule).

## 7. Cleanup (same session — one shared Neon dataset is live prod)

1. List the probe evidence and delete it by ID:
   ```bash
   curl -s "http://localhost:8000/api/db/triage/<DEMO_CASE_ID>/evidence" | python3 -m json.tool
   curl -s -X POST "http://localhost:8000/api/db/triage/<DEMO_CASE_ID>/evidence/batch-delete" \
     -H 'Content-Type: application/json' \
     -d '{"ids": [<probe row ids>]}'
   ```
   (The Database tab's case-level evidence browser does the same interactively.)
2. Verify net-zero: the case's `splunk_auto` rows are gone (Probe B's row too,
   if you want the demo case pristine). Re-run `map`-style checks from step 5
   only if you kept rows.
3. **Splunk side:** nothing — the connector never wrote.
4. Optional demobilization: set `SEARCH_BACKEND`→`mock` in BWS, `scripts/pull-secrets`,
   `bash scripts/start`. The rehearsal is repeatable anytime; the token can
   stay in the vault/keychain (it is inert unless `SEARCH_BACKEND=splunk`).
5. `tool_runs` audit rows: leave them — they are the audit record. (Role-gated
   `DELETE /api/jobs` exists if policy ever demands removal.)

## 8. Exit criteria and record-back

All of: Probe A success · Probe B (or explicit no_results pass) · every
contract row in §5 shows its expected result · failure drill shows
`query_failed` end-to-end · cleanup returns the case to its pre-rehearsal
evidence state.

Record: date, Splunk version, token scope, any normalization surprises →
`docs/DEVELOPMENT_PLAN.md` §5 ("Remaining" line) and §11 D2 → done; note in
`docs/RELEASE_NOTES.md` known-open list.

## Rollback

`SEARCH_BACKEND=mock` is the factory default: revert the BWS value (or unset
the export), restart, and the platform is back on the deterministic mock loop.
No schema, code, or Splunk-side changes to undo. The keychain/vault entry can
stay (inert while `SEARCH_BACKEND=mock`).
