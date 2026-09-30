# SOC Platform: AI Analysis, Detection Science, and Phase 2 SPL Workflow

## Overview

This document summarizes the enhancements made to the SOC Automation Platform around AI-assisted triage, detection-science enrichment, closure-note history, and second-phase SPL investigations.

The goal is to:
- Fuse detection science, rule templates, and closure history directly into AI analysis.
- Make supportive SPL queries first-class both pre- and post-analysis.
- Give analysts a structured way to run "Phase 2" queries, feed results back into AI, and promote good queries into the permanent rule toolkit.

## Detection Science in AI Analysis

The `/api/db/analyze` endpoint now:
- Anchors on `ESCorrelationRule` using `rule_id` or `rule_name`.
- Injects detection-science metadata into the prompt:
  - Rule ID/name, description/hypothesis, category, severity.
  - Drilldown fields and required closure fields.
  - Closure template ("Standard Closure Format").
- Includes:
  - Source notable evidence (parsed fields + sanitized text).
  - Historical baseline notables for the same rule.
  - Stored supportive query results (`supportive_query_results`).
  - Prior closure notes for the same rule (status + derived disposition label + generated note).

The AI is instructed to return a structured analysis with sections:
1. Initial Thoughts
2. Key Questions
3. Investigative Analysis
4. Supportive Query Recommendations (Phase 2 SPL)
5. Triage Verdict
6. Structured Closure Notes

## Closure Notes and History

- Closure templates and required fields live in `ESCorrelationRule` and are used by `/api/db/closure-note`.
- When analyzing a case, `/api/db/analyze` pulls prior `ClosureNote` records for the same `rule_id` and includes them as "Prior Closure Note Examples for this Rule" in the model prompt.
- Each prior closure is surfaced with:
  - Case ID, status, derived disposition label.
  - Analyst notes.
  - Structured/generated closure note text.

This gives the model examples of how similar alerts have been dispositioned and documented historically.

## Supportive Queries (Pre-Analysis)

- `GET /api/db/rules` returns all enabled `ESCorrelationRule` entries plus any `SupportiveQuery` rows for each rule.
- The Analysis tab uses `analysisRule.supportive_queries` to show:
  - A list of supportive SPL cards per rule.
  - A **Manage** button that opens the Supportive Query editor.
- The Supportive Query editor (`Manage Supportive Queries`):
  - Calls `GET /api/db/supportive-queries?rule_id=<rule_id>`.
  - Lets analysts add/edit/delete individual supportive SPL snippets.
  - Persists changes via `POST/PUT/DELETE /api/db/supportive-queries`.
  - Refreshes rule metadata so supportive cards stay in sync.
  - **Ordering:** cards are rendered in alphabetical order by *title* for a given rule. To enforce a specific sequence, prefix titles with numbers such as `1.`, `2.`, `3.` (e.g., `1. Primary drill-down`, `2. Raw evidence`, `3. Enrichment summary`).

### Placeholder Aliases

- Both supportive and Phase 2 SPL use placeholder aliases (e.g., `$host$`, `$dest$`, `$process$`).
- Aliases come from:
  - `placeholder_aliases` table, managed via `/api/db/placeholder-aliases`.
  - Fallback defaults when no aliases exist.
- The UI provides a **Manage Placeholder Aliases** dialog to:
  - Add/update/remove alias-to-field mappings per environment.
  - Control how tokens are resolved from `analysisSourceNotable.fields`.

## Phase 2 SPL Recommendations

After the first `/api/db/analyze` run for a case:

- The model is instructed to emit both:
  - A human-readable "Supportive Query Recommendations (Phase 2 SPL)" section.
  - A machine-readable JSON block (`PHASE2_QUERIES_JSON_START/END`) with entries:
    - `title`, `spl`, `description`.
- The backend parses this JSON into `phase2_queries` and returns it alongside the analysis text.

### Phase 2 UI Behavior

In the Analysis tab, when `phase2_queries` are present:

- A "Phase 2 SPL Recommendations" section appears with:
  - A **Manage Placeholder Aliases** button for quick alias adjustments.
  - One card per Phase 2 query with:
    - Title and description.
    - **Editable** SPL textarea, pre-populated with the model-suggested `spl`.
    - A results/notes textarea for pasted key rows or summaries from Splunk.
    - A **Copy SPL** button that:
      - Uses the edited SPL text.
      - Resolves placeholders via the same alias logic as supportive queries.
    - A **Save as Supportive** button that:
      - Persists the edited SPL as a `SupportiveQuery` for the current rule via `POST /api/db/supportive-queries`.
      - Refreshes rules so the query becomes part of the permanent supportive set.

### Phase 2 Analysis Loop

- The **Move to Phase 2 analysis** button:
  - Builds a new context string from:
    - The base `analysisContext` notes.
    - Any Phase 2 results pasted into the Phase 2 cards, under a dedicated header.
  - Calls `/api/db/analyze` again with the same case and current model.
  - The response is guarded by `analysisRequestId` so stale responses are ignored.
  - If the new response does not include a `phase2_queries` JSON block but a prior analysis did, the previous `phase2_queries` list is preserved so cards do not disappear.

## Analysis Cancellation and Model Switching

- The Analysis tab now exposes a **Cancel Analysis** button while an analysis is running.
- Under the hood:
  - Both `runAnalysis` and `runPhase2Analysis` increment a monotonically increasing `analysisRequestId`.
  - Responses only update `analysisResult` if their `requestId` matches the current `analysisRequestId`.
  - `cancelAnalysis()` increments `analysisRequestId` and clears the running flag, causing any late responses to be ignored.
- This allows analysts to:
  - Stop waiting on a hung or slow model. Model selection auto-resolves: an explicitly requested tag is used only if installed, otherwise the best available `llama3.1` tag is picked (override with `OLLAMA_MODEL`).
  - Switch to a different model and rerun analysis without losing pasted supportive or Phase 2 results.

## Recommended Analyst Workflow

1. **Initial Triage**
   - Select a case and model.
   - Review the detection-science block, historical baselines, and prior closure examples.
   - Optionally paste supportive query results and run initial analysis.

2. **Phase 2 Investigation**
   - Review Phase 2 SPL recommendations.
   - Edit SPL as needed and run in Splunk.
   - Paste key results into the Phase 2 cards.
   - Click **Move to Phase 2 analysis** to let the AI reconsider with new evidence.

3. **Promote Good Queries**
   - For any Phase 2 SPL that proves useful, click **Save as Supportive**.
   - The query becomes part of the rule's permanent supportive toolkit for future notables.

4. **Closure**
   - When satisfied, move to the Closure tab.
   - Use the rule templates, required fields, and suggested disposition to generate and persist closure notes for the case.
