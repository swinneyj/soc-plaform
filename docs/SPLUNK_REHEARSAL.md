# Live Splunk Rehearsal (D2)

*Assembled Sept 30, 2026 (execution-playbook stage D2). One-time rehearsal of
the Phase 3 read-only Splunk connector (`RealSplunkBackend`) against a real
Splunk. Blocked only on credentials — everything else is turnkey.*

**Secrets rule:** `SPLUNK_TOKEN` is a credential. It travels by BWS vault,
keychain injection, or a call — never pasted into chat, docs, or commits.

## 1. Get credentials (Splunk admin)

Request a **read-only** service account: a token with the `search` capability
only — no saved-search management, no index writes, no ES notable updates
(those are explicit v1 non-goals for the connector).

**Expected:** a base URL like `https://splunk.example.com:8089` and a bearer
token, delivered through BWS or a call.

## 2. Configure and restart

```bash
# Values go into the BWS vault / keychain injection — never into .env in Git.
export SEARCH_BACKEND=splunk
export SPLUNK_URL='https://splunk.example.com:8089'   # base URL, no path
export SPLUNK_TOKEN='<bearer token>'                  # from step 1
./scripts/start
```

**Expected:** startup completes; `scripts/start --check` reports the API up on
`127.0.0.1:8000`. (Unconfigured `SEARCH_BACKEND=splunk` fails loudly at
construction — `SplunkSearchError`, "requires SPLUNK_URL + SPLUNK_TOKEN" — so
a silent mock fallback is not possible.)

## 3. Run one Stage-2 query through the UI

Open the analysis wizard on a case with a supportive query card and press
**Run in Splunk** (or **Run All**). The request goes
`POST /api/splunk/search-one` → placeholder substitution (`$host$`, `$user$`,
…) from the case's notable fields → `RealSplunkBackend` executes the SPL via
`POST {SPLUNK_URL}/services/search/jobs/export` (bearer auth, single
round-trip) → CSV rows are normalized → results save to the evidence ledger
with the `splunk_auto` label and land in the card notes.

**Expected:** the card flips to `Saved ✓`, and the Evidence Timeline gains one
`search_one / splunk_auto` row for that query title (a re-run replaces the
prior auto row rather than duplicating it).

## 4. Verify connector behaviors

- **CSV normalization:** Splunk's `_time` epoch values become naive-UTC ISO
  strings in the persisted `timestamp` column; check one row's timestamp
  against the raw event. **Expected:** ISO format, correct instant.
- **Stats-row shape:** if the query ends in `| stats …` (no `_raw` field),
  the row still normalizes — fields default into the standard shape rather
  than being dropped. **Expected:** saved row present with its stats fields.
- **Bearer header on the wire:** the export request carries
  `Authorization: Bearer <token>`. **Expected:** appears in the Splunk
  `_internal` audit log (or `index=_audit` REST search) with HTTP 200 on
  `/services/search/jobs/export`.
- **Boundary-latch interplay:** the platform boots in `quarantined` boundary
  mode by default (`SPLUNK_BOUNDARY_MODE`), which restricts data *ingest*.
  Read-only searches must NOT be blocked by the latch. **Expected:** step 3
  succeeded while boundary status (SplunkBoundaryWidget) still shows
  quarantined.
- **Concurrency guard:** press Run on a card that is already running.
  **Expected:** HTTP 409 (one in-flight job per case), inline status stays
  `running`, no duplicate ledger row.
- **Audit trail:** the executed query text is logged to the `tool_runs` table
  (`tool_name=splunk_search_one`). **Expected:** the row exists with the exact
  substituted SPL.

## 5. Failure drill

Set a deliberately wrong token (`SPLUNK_TOKEN=bad`), restart, re-run step 3.

**Expected:** the connector raises `SplunkSearchError`; `map_search_outcome`
maps it to `query_failed`; the UI card shows the **Run failed** inline status
with no crash, and no evidence row is saved for the failed query. Restore the
real token and restart to finish.

## Exit criteria

All five steps show their expected results (including the two failure-path
expectations in steps 4–5). Record the date, the Splunk version, and any
normalization surprises in `docs/DEVELOPMENT_PLAN.md` §5 ("Remaining: live
rehearsal") and flip the roadmap item to done.
