# SOC Automation Platform
## Standard Operating Procedure for New Users

**Version:** 1.0  
**Last updated:** September 11, 2026  
**Audience:** SOC analysts, incident responders, detection engineers, and developers supporting the platform

---

## 1. Purpose

This SOP explains how to start the SOC Automation Platform, select a case, run the investigation workflow, record evidence, resolve blockers, produce a verdict, and generate a closure note.

The platform is local-first. It uses a local PostgreSQL database for case and evidence state and a local Ollama model for AI analysis. The database and analysis history are intended to survive application restarts and allow an analyst to resume work later.

Use only authorized telemetry and approved test data. Do not paste passwords, tokens, private keys, or unnecessary personal information into the platform.

---

## 2. What the platform includes

The top navigation contains the following areas:

| Area | Purpose |
|---|---|
| **Tools** | Browse and run registered SOC tools. Review tool status and output. |
| **Database** | Review triage records, notable events, statistics, and stored case data. |
| **AI Analysis** | Run the initial assessment and evidence-driven follow-up investigations. |
| **Closure Notes** | Select a closure rule and generate a structured case handoff note. |
| **Jobs** | Review queued and completed tool executions, including status and output. |
| **Reports** | View and download generated reports and analysis artifacts. |
| **Code Review** | Submit code or project material to the local Ollama review workflow. |

The investigation workflow is:

```text
Evidence Collection
        ↓
Initial Assessment
        ↓
Phase 2 Follow-Up
        ↓ blockers remain
Phase 3, Phase 4, or later targeted follow-up
        ↓
Final Verdict & Closure
        ↓
Structured Closure Notes
```

Additional phases are created only when unresolved questions or evidence-quality blockers remain.

---

## 3. Prerequisites

Before using the platform, confirm that:

- PostgreSQL is running and the platform database is available.
- Ollama is running locally.
- The required model is installed, normally `llama3.1:latest`.
- The API is healthy.
- You are working from the correct project checkout and branch.

For the standard Docker setup, the default services are:

```text
API:       http://127.0.0.1:8000
Database:  localhost:5433
Ollama:    http://127.0.0.1:11434
```

For the native Mac API test setup, the API commonly runs on port `8001` and PostgreSQL on port `5432`.

---

## 4. Starting the platform

### 4.1 Docker-based start

From the project directory:

```bash
set -a
source .env
set +a

ollama serve
ollama pull llama3.1:latest
./scripts/start_platform.sh
```

Open:

```text
http://127.0.0.1:8000/index.modular.html
```

Verify API health:

```bash
curl http://127.0.0.1:8000/health
```

### 4.2 Native API restart

For the native virtual-environment setup:

```bash
export DATABASE_URL='postgresql+psycopg://mini@localhost:5432/soc_platform'
./scripts/restart_api.sh
```

The password should be supplied by the local PostgreSQL credential configuration, such as `~/.pgpass`, rather than committed to a shell history or repository file.

After a backend change, restart the API. After a frontend-only change, a hard refresh is normally enough:

```text
Cmd + Shift + R
```

### 4.3 Stopping the platform

Use the project stop helper when available:

```bash
./scripts/stop_platform.sh
```

Use `docker compose down` to stop services while preserving database data. Do not use `docker compose down -v` unless you intentionally want to delete the database volume.

---

## 5. Case input expectations

The AI workflow works best when the case contains enough structured context to support a specific investigation. Before starting analysis, provide:

- A unique case ID.
- A concise title.
- Alert or detection name.
- Severity.
- Event timestamp or review window.
- Host, user, source, and relevant process fields.
- Detection rationale or triggering condition.
- Available raw event details.
- Any known business or maintenance context.
- The desired initial disposition, if already known.

Good input is specific and evidence-oriented:

```text
Case ID: TEST-POWERSHELL-1
Title: PowerShell In-Memory Execution
Host: WIN-APP-042
User: admin.jlee
Severity: Medium
Trigger: Encoded PowerShell launched in memory using -Enc
Parent process: svchost.exe
Review window: 2026-09-10 23:00–23:59 UTC
```

Avoid unsupported conclusions in the source context. The AI should assess the evidence, not simply repeat a verdict supplied in the prompt.

---

## 6. Standard investigation procedure

### Step 1 — Select the case

Open **AI Analysis** and select the case from the active-case selector. Confirm that the title, host, user, and review context match the incident you intend to investigate.

### Step 2 — Run the initial assessment

Start the initial analysis. Review:

- Initial thoughts or threat hypothesis.
- Key questions to answer.
- Investigative analysis.
- Preliminary disposition.
- Confidence.

Treat the initial result as a working hypothesis. A result such as `Malicious — 75%` is not closure-ready by itself.

The key questions generated here become the blockers that later follow-up phases must address.

### Step 3 — Review Phase 2 recommendations

Phase 2 should contain targeted SPL recommendations grounded in the initial analysis. Each query should have:

- A clear query title.
- A specific reason it was recommended.
- The open inquiry or inquiries it targets.
- Editable SPL text.
- A results/notes area.
- Result status.
- Finding direction.
- Inquiry resolution.

If an SPL query does not work in the analyst’s environment, edit the query before copying it to Splunk. Preserve the final edited query in the evidence record so the audit trail reflects what was actually run.

### Step 4 — Run and record evidence

For each query:

1. Copy or edit the SPL query.
2. Run it in the authorized search environment.
3. Paste the meaningful result into **Results / Notes**.
4. Select the correct **Result Status**.
5. Select the finding direction:
   - **Supports** — strengthens the current disposition or hypothesis.
   - **Refutes** — weakens the current disposition or hypothesis.
   - **Neutral** — relevant, but does not materially change the assessment.
6. Select **Inquiry Resolution**:
   - **Does Not Resolve**
   - **Partially Resolves**
   - **Resolves Inquiry**
7. If resolving an inquiry, select the specific inquiry.
8. Save the evidence.

Do not save a blank result or a placeholder such as `Pending analyst observation`. A successful query with no substantive observation becomes a closure blocker.

### Step 5 — Re-analyze the case

Use **Save New Evidence & Re-Analyze** when the new result is intended to affect the AI’s assessment. Use **Save Phase Evidence** when you are preserving evidence without starting another model run.

After analysis completes, review the updated confidence, inquiry status, and recommended next actions.

### Step 6 — Continue forward through new phases

If blockers remain, Stage 5 presents a button such as **Start Phase 3 Follow-Up** or **Start Phase 4 Follow-Up**. Use that button to advance to the next targeted investigation phase.

Do not restart the case from the beginning just because one question remains unresolved. Prior analysis and saved evidence are carried forward automatically.

Each phase contains its own:

- Targeted query set.
- Evidence source label, such as `phase3_manual`.
- Open inquiries targeted.
- Saved evidence history.
- Analysis result.

Resolved inquiries leave the active list but remain available in the audit trail. Use **Show Resolved** or **Add More Evidence** when additional validation is useful.

### Step 7 — Resolve every blocker

Continue until the case shows:

```text
Active Investigation Blockers: 0
Unresolved Open Questions: 0
```

If a blocker says that saved evidence lacks a substantive observation, return to the phase that created it, add the actual result, choose the status and resolution fields, and re-analyze.

### Step 8 — Review the final verdict

The case is normally ready for closure when the closure gates are satisfied:

- Confidence meets the configured threshold.
- There are enough substantive corroborating findings.
- Active investigation blockers are zero.
- Unresolved open questions are zero.

Review the evidence ledger before proceeding. The ledger should show the query title, source phase, status, direction, and a readable observation for each important finding.

### Step 9 — Generate the closure note

Open **Closure Notes** and:

1. Select the case.
2. Select the applicable closure rule from the rule catalog.
3. Complete the required justification and disposition fields.
4. Add concise analyst notes.
5. Generate the structured closure note.
6. Review the note for accuracy and remove unsupported claims.
7. Copy or download the note according to local case-management procedures.

The closure note should summarize the alert, evidence, disposition, confidence, investigative actions, unresolved risk, and recommended follow-up.

---

## 7. Sample test case

The project was validated with this synthetic case:

```text
Case: TEST-POWERSHELL-1 — PowerShell In-Memory Execution (malicious)
Host: WIN-APP-042
User: admin.jlee
Disposition: Malicious
```

### Sample encoded-payload evidence

```text
_time                 host        user       Message
2026-09-10 23:40:18   WIN-APP-042 admin.jlee powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA...

ScriptBlockText:
Decoded payload launches an in-memory PowerShell command that uses
Net.WebClient.DownloadString against https://updates.corp.example/payload.ps1
and executes the returned content with IEX.

ParentImage: C:\Windows\System32\svchost.exe
ParentCommandLine: svchost.exe -k netsvcs
ProcessId: 4180
```

Recommended classification for this synthetic result:

```text
Result status: Success
Finding direction: Supports
Inquiry resolution: Resolves Inquiry
Inquiry: What is the purpose of the encoded payload being executed?
```

### Sample host/user pivot result

```text
2026-09-10 23:41:12 WIN-APP-042 admin.jlee Get-MpComputerStatus | Select-Object AMRunningMode.RealTimeProtectionEnabled
2026-09-10 23:42:03 WIN-APP-042 admin.jlee Invoke-WebRequest -Uri https://updates.corp.example/maintenance.ps1
2026-09-10 23:40:18 WIN-APP-042 admin.jlee powershell.exe -NoP -NonI -W Hidden -Enc SQBFAFgA...

No additional suspicious in-memory PowerShell executions were identified on WIN-APP-042 during the review window.
```

Recommended classification:

```text
Result status: Success
Finding direction: Refutes
Inquiry resolution: Resolves Inquiry
Inquiry: Are there any other suspicious PowerShell executions on the same host?
```

### Sample script-hash result

```text
_time                 host        user        distinct_hashes  hashes
2026-09-10 23:40:18   WIN-APP-042 admin.jlee  1                 7f3a9c2e1b4d6a8f9c0e11223344556677889900

The hash was not found in the authorized script baseline or known-good PowerShell catalog.
No matching maintenance ticket or approved software deployment was identified.
```

Recommended classification:

```text
Result status: Success
Finding direction: Supports
Inquiry resolution: Resolves Inquiry
Inquiry: Is this a known or unknown PowerShell script?
```

This data is synthetic test content and must not be presented as real production telemetry.

---

## 8. Persistence and resuming work later

Analysis results, phase evidence, inquiry resolutions, and closure-gating state are stored in PostgreSQL. A user can stop the platform and resume the same case later as long as the database volume or database dump is preserved.

For a shared test dataset:

- Import the provided SQL dump into each user’s local `soc_platform` database.
- Do not commit the dump or `.pgpass` to Git.
- Use `SKIP_SHARED_DUMP_RESTORE=1` after the first import so a normal restart does not overwrite new work.
- Keep each user’s PostgreSQL password local.

The database name does not need to match the Mac login name. The standard project database name is `soc_platform`.

---

## 9. Troubleshooting

### The page is loading but analysis does not complete

Check that Ollama is running and the configured model exists:

```bash
ollama list
```

Then check API health and review the API terminal output.

### The browser still shows old UI content

Use a hard refresh (`Cmd + Shift + R`). Restart the API only when backend code or configuration changed.

### PostgreSQL authentication fails

The Mac account password and PostgreSQL role password are different. Test the database directly:

```bash
psql -h localhost -p 5432 -U mini -d soc_platform -c "select 1;"
```

If using `.pgpass`, the entry must match host, port, database, and role exactly, and the file must be private:

```text
localhost:5432:soc_platform:mini:YOUR_POSTGRES_PASSWORD
```

```bash
chmod 600 ~/.pgpass
```

### The API says `DATABASE_URL` is not set

Load the environment before starting the API:

```bash
set -a
source .env
set +a
```

Or export the URL for the current shell. Do not place a real password in a committed file.

### Stage 5 still shows a blocker

Read the blocker text carefully:

- **Unresolved open questions:** start the next follow-up phase and resolve the named inquiry.
- **Evidence lacks substantive observations:** return to the relevant evidence card and replace the placeholder with the actual result.
- **Confidence below threshold:** review all evidence and continue investigation; do not force closure.

### A follow-up query does not work in Splunk

Edit the SPL in the query card, run the corrected version, and save the edited query with its actual output. The platform is designed to preserve the analyst’s final query and evidence rather than requiring the generated query to be used unchanged.

---

## 10. Analyst quality checklist

Before moving a case to closure, confirm:

- [ ] The correct case and review window are selected.
- [ ] The initial hypothesis is supported by source evidence.
- [ ] Every active inquiry has a targeted query or documented reason it is not applicable.
- [ ] Every saved evidence card has a substantive observation.
- [ ] Result status and finding direction are correct.
- [ ] Inquiry resolution is assigned to the specific inquiry it addresses.
- [ ] Resolved inquiries are no longer active blockers.
- [ ] The evidence ledger contains the important query results.
- [ ] Confidence and disposition are reviewed by an analyst.
- [ ] The closure rule matches the final disposition.
- [ ] The generated closure note contains no unsupported or sensitive content.

---

## 11. Where to look in the repository

For maintainers, the primary locations are:

| Need | Location |
|---|---|
| Browser UI | `web/index.html` and `web/app.js` |
| API routes | `api/main.py` |
| Database models | `db/models.py` |
| Ollama integration | `services/ollama_service.py` |
| Runtime scripts | `scripts/` |
| Generated reports | `Data/Reports/` |
| Shared database handoff | `local-backups/shared-db/` |
| Navigation map | `docs/REPO_MAP.md` |

For feature-specific ownership, use `docs/REPO_MAP.md` before changing code.

