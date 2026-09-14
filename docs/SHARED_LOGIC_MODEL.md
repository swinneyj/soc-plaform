# Shared Logic Model

## Goal

Keep shared logic in Git.
Keep local alert and event data in each user's local PostgreSQL unless explicitly exported.

## Shared In Git

These should be treated as the shared source of truth:
- `sample_rules.json`
- `supportive_rules.json`
- tool scripts under `Tools/`
- closure templates and baked logic in code
- workflow and playbook JSON

## Local In PostgreSQL

These do not need to be shared live by default:
- `triage_results`
- `splunk_events`
- `analysis_results`
- `closure_notes`
- `supportive_query_results`

## Sync Pattern

1. Change shared logic in Git-managed files.
2. Commit and push those files.
3. Other users pull the repo.
4. Other users run:

```powershell
.\scripts\sync_shared_logic_to_db.ps1
```

That updates their local PostgreSQL with the repo-backed rules and supportive SPL queries.

## Why This Works Better

This avoids treating the database as the source of truth for shared rules.

Benefits:
- rule logic is reviewable in Git
- supportive SPL queries are versioned
- users can work offline with local PostgreSQL
- only actual case and alert data needs dump-based sharing when necessary

## When To Export A Dump

Use a dump when you need to share:
- current alert history
- current triage results
- supportive query results already collected
- closure note history

Use:

```powershell
.\scripts\export_db_dump_to_share.ps1
```