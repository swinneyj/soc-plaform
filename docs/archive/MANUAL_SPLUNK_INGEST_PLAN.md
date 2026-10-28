# SOC Platform Manual Splunk Ingest Implementation Plan

## Goal

Implement a manual-first Splunk notable ingestion workflow in the SOC Platform that:

- accepts pasted Splunk notable content
- optionally stores rule metadata and SPL context with the notable
- deduplicates repeated ingest attempts
- links imported notables to rule logic earlier
- makes cases immediately usable for AI analysis and closure-note drafting
- keeps all analyst-impacting and detection-impacting actions behind human review

This plan assumes direct Splunk webhook/API ingestion is not available yet and that V1 should work through analyst paste/manual entry.

---

## Current State

The platform already has these working pieces:

- pasted notable save flow in the Database tab
- recent pasted notable review list
- promote-to-triage workflow
- AI analysis workflow
- supportive query management
- placeholder alias management
- phase-2 SPL recommendation display
- closure note generation

That means V1 should extend the existing pasted-notable pipeline, not replace it.

---

## V1 Scope

### In Scope

- manual paste of Splunk notables
- optional metadata fields supplied by analyst during paste
- stronger dedupe for pasted notables
- early rule linkage at ingest time
- storage of raw SPL / alert reasoning text when available
- supportive SPL attachment to a rule/case
- analysis-ready case promotion
- manual AI analysis trigger
- manual closure note generation and approval

### Out of Scope

- automatic Splunk webhook ingestion
- direct Splunk REST pulls
- automatic closure/dispositioning
- automatic tuning application
- automatic rule changes in Splunk

---

## Target Workflow

### 1. Manual Splunk Input

Analyst pastes:

- notable text from Splunk Incident Review
- optional event ID / case ID
- optional rule ID / correlation rule title
- optional raw SPL or saved-search reasoning
- optional source instance label

### 2. Platform Ingest

Platform:

- parses the pasted notable
- sanitizes/tokenizes it
- stores parsed fields and sanitized text
- stores optional metadata provided by analyst
- computes a dedupe key
- attempts rule linkage immediately

### 3. Review Queue

Saved notables appear in Recent Pasted Notables with:

- title
- correlation search
- disposition/status
- linked rule ID if resolved
- historical/open flag
- promoted/not promoted status

### 4. Promote To Triage

Promotion creates or links:

- triage case record
- stable case ID
- linked rule ID
- initial verdict/confidence seed

### 5. Analysis

Analysis consumes:

- triage case
- parsed notable fields
- sanitized notable text
- prior closure history if present
- raw SPL / detection reasoning if provided
- supportive SPL query definitions
- manually pasted supportive query results

### 6. Closure / Approval

Analyst reviews:

- AI analysis output
- phase-2 SPL suggestions
- closure note draft
- tuning ideas

Nothing detection-affecting auto-applies.

---

## Recommended File-Level Implementation

## Backend

### 1. Extend pasted notable payload

File:

- `api/main.py`

Update `PastedNotableRequest` to optionally accept:

- `event_id`
- `case_id`
- `rule_id`
- `rule_title`
- `raw_spl`
- `saved_search_name`
- `source_instance`
- `ingest_method`

Recommended default:

- `ingest_method = "manual_paste"`

### 2. Persist richer notable metadata

File:

- `api/main.py`

Inside the pasted-notable save handler, include the optional metadata in the saved event payload written into `SplunkEvent.raw`.

Recommended V1 choice:

- continue using `SplunkEvent` instead of creating a brand-new table immediately
- treat `raw` JSON as the richer ingest envelope for now

### 3. Improve dedupe

Files:

- `api/main.py`

Current state:

- historical notables have composite dedupe logic
- open/current notables do not

Add V1 dedupe precedence:

1. exact `event_id`
2. exact `case_id`
3. exact `rule_id + notable_time + host`
4. fallback normalized sanitized-text hash

Apply to both historical and non-historical pasted notables.

### 4. Link rule at ingest time

Files:

- `api/main.py`

Current state:

- rule resolution happens later during promote-to-triage

V1 change:

- resolve and store `rule_id` during the paste/save step whenever possible
- preserve analyst-supplied `rule_id` if one is provided
- if only rule title/correlation search is present, attempt the current exact/contains matching logic there

### 5. Add placeholder alias suggestions route

Files:

- `api/main.py`

Current state:

- frontend expects alias suggestions from recent notables
- local backend route is missing

Add endpoint:

- `GET /api/db/placeholder-aliases/suggestions`

Suggested behavior:

- scan recent pasted notable `fields`
- count recurring parsed field names
- return sorted candidate list with counts
- optionally bias toward fields not already mapped in `placeholder_aliases.json`

### 6. Feed richer context into analysis

Files:

- `api/main.py`

Update analysis prompt assembly so it includes, when present:

- stored `raw_spl`
- saved search name
- source instance
- rule title / rule ID
- pasted notable history
- prior closure notes
- supportive query results

This is the biggest path toward lowering token waste, because better structured inputs reduce repeated manual explanation.

---

## Database / Model Direction

### V1 Recommendation

Do not add a new table yet unless necessary.

Use existing models:

- `SplunkEvent` for stored pasted notables
- `TriageResult` for promoted cases
- `SupportiveQuery` for reusable rule-bound SPL
- `SupportiveQueryResult` for pasted query results
- `AnalysisResult` for AI outputs
- `ClosureNote` for closure-note drafts
- `PlaceholderAlias` for token/field mappings

### V2 Candidate

If manual ingest becomes central, add a dedicated `PastedNotableRecord` or `NotableIngestRecord` model later so metadata is queryable without unpacking JSON.

---

## Frontend / GUI Changes

File:

- `web/index.html`

### 1. Extend the Paste Splunk Notable form

Add optional fields under the paste textarea:

- Event ID
- Case ID
- Rule ID
- Rule Title / Correlation Title
- Saved Search Name
- Raw SPL / Alert Logic
- Source Instance

Keep the current paste-only path valid so analysts can still just paste a notable and click save.

### 2. Show ingest metadata in Recent Pasted Notables

Display when present:

- event ID
- case ID
- rule ID
- saved search name
- ingest method
- source instance

### 3. Surface linkage state

Add a visible indicator such as:

- linked to rule
- not yet linked
- analyst-supplied rule ID
- fuzzy-matched rule ID

### 4. Keep promotion explicit

Do not auto-promote on save in V1.

Reason:

- analysts still need a checkpoint to confirm the paste parsed correctly

### 5. Preserve analysis workflow shape

Keep these existing analyst-driven steps:

- select case
- run AI analysis
- review supportive SPL
- paste supportive results
- move to phase 2 analysis
- move to closure notes

That workflow already matches the safer human-review model.

---

## Supportive SPL / Alert Logic Strategy

### Reusable supportive logic

Use `SupportiveQuery` for:

- stable queries tied to a rule ID
- analyst-maintained follow-up SPL
- expected repeatable evidence gathering

### Case-specific logic

Use stored `raw_spl` or phase-2 queries for:

- notable-specific alert reasoning
- one-off query ideas
- evolving investigation-specific follow-ups

### Important design rule

Do not treat every pasted SPL fragment as a reusable rule query automatically.

Reason:

- reusable supportive logic should stay curated
- one-off alert reasoning should remain case context until reviewed

---

## Dedupe and Identity Rules

Recommended V1 identity precedence:

1. analyst-provided `event_id`
2. analyst-provided `case_id`
3. resolved `rule_id` + parsed time + host/destination
4. normalized hash of sanitized text

Recommended behavior:

- if exact match found, update existing record metadata instead of creating a duplicate
- preserve analyst-added metadata if the newer paste is richer
- for historical notables, remain conservative and reuse existing record aggressively

---

## Human Approval Gates

Keep these actions manual in V1:

- promote to triage
- run AI analysis
- save supportive query definitions
- accept phase-2 SPL suggestions
- generate closure note
- choose final disposition
- apply any tuning idea outside the platform

Nothing in Splunk detection logic should auto-change from this workflow.

---

## Best First Sprint

If time is limited, do these first:

### Sprint 0

1. Define a strict AI-generated SPL placeholder contract
2. Update the analysis prompt so phase-2 SPL uses only supported placeholders such as `$host$`, `$dest$`, `$user$`, `$process$`, and `$source_ip$`
3. Fix phase-2 SPL rendering/copy flow so copied queries are guaranteed to resolve only supported placeholders
4. Reject or visibly flag unsupported placeholder/token output in suggested SPL instead of silently copying broken queries
5. Test copied phase-2 SPL against real Splunk analyst usage before expanding ingest complexity

Reason:

- the current GUI can substitute known `$placeholder$` values into supportive SPL
- the current AI phase-2 SPL path does not broadly re-tokenize arbitrary model output
- if the model emits unsupported placeholders or sanitized token artifacts, the copied SPL will not run correctly
- this makes the SPL output path a prerequisite for broader ingest/tokenization work

### Sprint 1

1. Extend `PastedNotableRequest` with optional metadata
2. Persist metadata into saved notable payload
3. Implement missing alias-suggestions backend endpoint
4. Add dedupe for non-historical notables
5. Display richer metadata in Recent Pasted Notables

### Sprint 2

1. Link rule at ingest time
2. Thread rule metadata and raw SPL into analysis
3. Improve promotion logic to prefer stored rule linkage
4. Show linkage state in GUI

### Sprint 3

1. Analyst-friendly import of older notables for baseline enrichment
2. Better case-bundle display combining notable, supportive results, analysis, and closure
3. Optional queued analysis mode for newly promoted cases

---

## Questions For Splunk Expert Session

Use the expiring Splunk expert credits on the future automation path, not the manual V1.

Ask:

1. Best transport for notables plus rule context:
   - REST polling
   - scheduled export
   - saved-search output
   - webhook delivery

2. How to retrieve stable identifiers for dedupe:
   - notable ID
   - event ID
   - case ID
   - correlation search ID

3. Whether Splunk can provide both:
   - notable record data
   - correlation search / SPL / detection metadata

4. Best way to retrieve supporting search text for a given notable or rule

5. Recommended rate limits / batching patterns if automation is turned on later

---

## Final Recommendation

Build manual V1 now on top of the existing pasted-notable path.

Do not wait for webhook/API automation to start the process.

But before broader mass-tokenization work, fix the AI-generated SPL contract and rendering path first.

The strongest immediate path is:

- phase-2 SPL contract and rendering fix
- manual notable ingest
- richer metadata
- better dedupe
- early rule linkage
- curated supportive SPL
- manual AI analysis
- manual closure review

Then, when Splunk API access exists, swap the ingest source without redesigning the analyst workflow.
