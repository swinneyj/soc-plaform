# Archive — historical paperwork (not authoritative)

Nothing in this directory describes current behavior. These files were moved here
on **2026-10-23** because they had become stale, superseded, or unreferenced, and
a stale document is worse than no document: it reads like an instruction.

They are kept (not deleted) so the reasoning survives. `git log --follow` still
works on every one of them, and the full text is one `git show` away.

## What's here and why

| File | Why archived | Live replacement |
|---|---|---|
| `CHAT_HANDOFF.md` | Stale narrative handoff from the `justin`-branch / port-8001 era. Already flagged P1 by `docs/GAP_AUDIT_2026_09_30.md` (F6: "stale narrative handoff … mildly misleading onboarding surface"). | `AGENTS.md` (boot), `docs/NEW_DEVELOPER_ONBOARDING.md` |
| `CROSS_PLATFORM_PARITY_PUNCH_LIST.md` | A punch list, not a plan — its items either shipped or were folded into the roadmap. Zero inbound references. | `docs/DEVELOPMENT_PLAN.md` |
| `MANUAL_SPLUNK_INGEST_PLAN.md` | Sept-14 manual-ingest plan, superseded by the mock/real search backend split and the rehearsal flow. Zero inbound references. | `docs/SPLUNK_REHEARSAL.md`, `services/search_backend.py` |
| `SOC_PLATFORM_USER_SOP.md` | Operator SOP that still told readers the database lives on `localhost:5433` (the compose port). That is no longer the primary — local Postgres on 5432 is. | `docs/DAILY_WORKFLOW.md`, `OPERATOR_CHEAT_SHEET.md`, `docs/LOCAL_FIRST_DATABASE.md` |
| `git-flow-cli-readme.txt` | Vendor readme for a CLI nobody here runs; zero inbound references. | — |
| `daily-summaries/` | Two Aug-27 session write-ups from before the modularization; zero inbound references. | `docs/DEVELOPMENT_PLAN.md` history, git log |

## What is *not* here (and why)

* `docs/DEVELOPMENT_PLAN.md`, `docs/EXECUTION_PLAYBOOK.md`,
  `docs/security-remediation-tracker.md`, `AGENTS.md` — load-bearing; referenced
  from every entry point.
* `docs/VERCEL_HANDOFF.md` — retired-but-retained by owner decision; `AGENTS.md`
  points at it on purpose.
* Old **code** lives in `scripts/attic/`, `scripts/_archive_oneoffs/`, and
  `web/Old_archive/` — same principle, different directory.

## Working rule

Do not link into this directory from live docs. If something here is needed
again, copy the relevant fact into a live document first, then link to that.
