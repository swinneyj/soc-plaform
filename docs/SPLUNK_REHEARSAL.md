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
- [ ] Backend posture visible **without running a query** (E.3):
      `curl -s http://localhost:8000/api/splunk/search-status | python3 -m json.tool`
      → expect `"backend": "splunk"`, `"ready": true`, `"read_only": true`.
      Credentials show as booleans only (`splunk_url_configured` /
      `splunk_token_configured`); the endpoint never echoes the URL or token.
      `"ready": false` names the missing piece in `problem` — fix that before
      step 1.
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

### 0.5 Dry run without credentials (fake Splunk) — optional, ~2 min

`scripts/fake_splunk.py` is a stdlib-only stand-in for the Splunk management
port: search export as CSV (epoch `_time`), a saved-search listing with one
hidden entry, bearer-token auth (wrong token → 401), and the platform's own
bounded SPL subset executed via `MockSplunkBackend`. It proves the entire
*client* path over real HTTP — connector, CSV + epoch normalization, both
`/api/splunk/*` introspection routes — with zero credentials.

In a second terminal:

```bash
.venv314/bin/python scripts/fake_splunk.py --port 18089 --token dev-token
```

Then launch the platform pointed at it (dummy token only — never a real one):

```bash
SEARCH_BACKEND=splunk SPLUNK_URL=http://127.0.0.1:18089 SPLUNK_TOKEN=dev-token \
  bash scripts/start
curl -s http://localhost:8000/api/splunk/search-status | python3 -m json.tool   # ready: true
curl -s http://localhost:8000/api/splunk/saved-searches | python3 -m json.tool  # 1 visible fixture
```

The same setup also drives the **whole** search-one path — env → factory →
`RealSplunkBackend` → real socket → CSV parse → `splunk_auto` ledger row. Pick
a seeded demo case and press *Run in Splunk*, or:

```bash
curl -s -X POST http://localhost:8000/api/splunk/search-one \
  -H 'Content-Type: application/json' \
  -d '{"case_id":"<demo case>","query_title":"<stored card title>","earliest":"-7d"}' \
  | python3 -m json.tool     # backend: RealSplunkBackend, result_status: success
```

**Rehearse the bad days** (these are the failures the live run can hit, and
the fake can stage them on demand):

| Scenario | Fake invocation | Expected route outcome |
|---|---|---|
| Token lacks `search` capability | `--fail-status 403` | `result_status: query_failed`, `error` carries `HTTP 403` + Splunk's message, `ToolRun.status = failed` |
| Splunk busy / restarting | `--fail-status 503` | same shape, `HTTP 503` in `error` |
| Search runs long | `--delay 3` | connector timeout (or the route's per-run guardrail) → `query_failed`, never a hung case |
| Query matches nothing | normal run, narrow SPL | `no_results` (distinct from `query_failed` in the ledger) |
| Self-signed management port | `--tls-cert cert.pem --tls-key key.pem` (HTTPS) | bare run → `query_failed` naming `SPLUNK_CA_BUNDLE`; with `SPLUNK_CA_BUNDLE=<cert.pem>` → `success` |

```bash
# generate a throwaway cert for the loopback fake (test-only; never committed)
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout /tmp/fs-key.pem -out /tmp/fs-cert.pem \
  -subj "/CN=127.0.0.1" -addext "subjectAltName=IP:127.0.0.1"
.venv314/bin/python scripts/fake_splunk.py --port 18443 \
  --tls-cert /tmp/fs-cert.pem --tls-key /tmp/fs-key.pem
SEARCH_BACKEND=splunk SPLUNK_URL=https://127.0.0.1:18443 SPLUNK_TOKEN=dev-token \
  SPLUNK_CA_BUNDLE=/tmp/fs-cert.pem bash scripts/start
```

These are permanent tests now, not just a manual drill:
`tests/test_search_backend.py` runs the connector and the route over a real
loopback socket (82 tests in that file), including a wire log that asserts the
connector only ever issues `POST /services/search/jobs/export` and
`GET /services/saved/searches` — `read_only: true` is proved, not asserted.

**A fuller demo (500 events, seven indexes, a notable, six cards):**
`docs/FAKE_SPLUNK_DEMO.md` — same fake, narrative corpus generated by
`scripts/seed_demo_corpus.py`, runs the whole notable → case → cards →
`no_results` loop. Use it to rehearse the *platform*; use this document for the
*instance*.

**Scope guard:** this cannot satisfy the exit criteria below. It runs the same
SPL subset as the mock backend against fixture data with a fake auth model —
it de-risks the live run (proves our stack before real credentials exist), and
nothing more. The live rehearsal remains required.

## 1. Land the credentials (BWS → .env → keychain)

1. **BWS UI** (you or whoever holds the vault): org **SSC-Lewis** → project
   **SOC Platform** → add these to the machine account's scope:

   | Key | Value | Secret? |
   |---|---|---|
   | `SEARCH_BACKEND` | `splunk` | no (config) |
   | `SPLUNK_URL` | `https://splunk.example.com:8089` (base URL, no path) | no |
   | `SPLUNK_TOKEN` | the read-only bearer token (`search` capability only) | **yes** |
   | `SPLUNK_CA_BUNDLE` | optional: absolute path to the instance's PEM CA bundle (see §1.5) | no (a path) |
   | `SPLUNK_VERIFY_TLS` | optional: `false` **only** as a lab last resort (see §1.5) | no (config) |

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

### 1.5 TLS trust (only if the management port is self-signed)

Most on-prem Splunk deployments serve a certificate from an internal or
self-signed CA on `:8089`. The connector verifies certificates by default
(`certifi`), so a bare handshake fails with `CERTIFICATE_VERIFY_FAILED` and an
error that names `SPLUNK_CA_BUNDLE`.

**Preferred fix — pin the CA (verification stays on, for this connection only):**

```bash
# copy the instance's CA (or its own server cert if self-signed) to a stable path
curl -sk https://splunk.example.com:8089/services/server/info \
  | python3 -m json.tool >/dev/null      # optional reachability check, token not needed
openssl s_client -connect splunk.example.com:8089 -showcerts </dev/null 2>/dev/null \
  | openssl x509 -outform PEM > ~/.soc/splunk-ca.pem
chmod 644 ~/.soc/splunk-ca.pem
export SPLUNK_CA_BUNDLE="$HOME/.soc/splunk-ca.pem"   # or put it in BWS as a path
```

Then verify the posture **before running a query** — the status endpoint
reports it as booleans and fails readiness on a bad path:

```bash
curl -s http://localhost:8000/api/splunk/search-status | python3 -m json.tool
#   "ready": true,
#   "tls": {"verify": true, "verification_disabled": false,
#           "ca_bundle_configured": true, "ca_bundle_readable": true,
#           "ca_bundle_certificates": 1,
#           "ca_bundle_expires_at": "2027-11-27T15:12:40+00:00",
#           "ca_bundle_days_remaining": 399,
#           "ca_bundle_expired": false,
#           "ca_bundle_warning": ""}
```

**Read the expiry, not just the booleans.** The endpoint parses the pinned bundle
and reports its **earliest** certificate expiry, so a CA rotation is caught at
status time instead of mid-investigation:

| `ready` | `tls` signal | Meaning |
|---|---|---|
| `true` | `ca_bundle_days_remaining: 399`, no warning | Fresh — nothing to do |
| `true` | `ca_bundle_warning: "…expires in 11 day(s)…"` | Still works; **rotate now** (warns at ≤ 30 days) |
| `false` | `ca_bundle_expired: true`, `problem` naming the date | Handshake would fail — replace the bundle before probing |

The `ca_bundle_days_remaining` value can be negative (already expired). For a
bundle *directory*, the soonest-expiring member wins. If the bundle exists but
holds no parseable PEM, readiness fails with that reason named — a state that
would otherwise surface as a handshake error later.

**Last-resort escape hatch:** `SPLUNK_VERIFY_TLS=false` turns verification off
for this connector only (never process-wide). It logs a warning at construction
and shows up as `"verify": false, "verification_disabled": true` in
`search-status` — which is deliberate, so the decision is visible rather than
silent. Use it only when the certificate cannot be pinned (e.g. a lab box),
and record it in `docs/security-remediation-tracker.md`.

Precedence: a valid `SPLUNK_CA_BUNDLE` always wins — if both are set, the
bundle is used and verification stays on.

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
| **Saved-search listing (E.3)** | `curl -s http://localhost:8000/api/splunk/saved-searches` | HTTP 200 with `{"success": true, "count": N, "items": [{"name", "description", "search"}]}` — visible searches only, sorted by name. `409` while `SEARCH_BACKEND=mock`: that is the wrong-mode guard working, not a failure. |
| **Bearer on the wire** | In Splunk: `index=_audit sourcetype=audittrail` filtered to your user | `POST /services/search/jobs/export` → HTTP 200, bearer auth |
| **TLS trust** | Before any probe: `curl -s http://localhost:8000/api/splunk/search-status` | `tls.verify: true`; `ca_bundle_expired: false` with `ca_bundle_warning: ""` (a warning at ≤ 30 days is a rotation task, not a failure); `verify: false` means the escape hatch is on — record why |
| **TLS failure shape** | Point `SPLUNK_CA_BUNDLE` at a wrong file, restart, run Probe A | `result_status: query_failed`, `error` contains `Splunk TLS verification failed` and names `SPLUNK_CA_BUNDLE` — never a raw traceback |
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
