# Vercel Handoff Package (D1) — RETIRED

*Assembled Sept 30, 2026 (execution-playbook stage D1). Retired the same day,
by owner decision: the hosted Vercel deployment is not needed — the platform
runs fully locally (`scripts/start`, mock-Splunk default) and the stated goal
(local fake-Splunk testing → eventual real-Splunk import) never leaves this
machine. The env-var handoff, routing question, and deploy checklist below are
all moot without a deploy.*

## What was retired (Sept 30, 2026)

- **`.github/workflows/deploy.yml` deleted.** Pushes to `main` / `dev` /
  `dev-dalton` / `dev-justin` no longer deploy anywhere; the last-push-wins
  `--prod` deploy and its `/api/health` smoke test are gone with it.
- **§1 env vars, §2 routing question, §5 checklist — moot.** No deploy means no
  `DATABASE_URL` / `API_KEY` / `CORS_ORIGINS` / `ENABLE_DOCS` to set in Vercel
  and no CDN-vs-function injection decision to make.
- **Status of the running deployment:** the last-built (pre-rotation) bundle
  still serves `soc-plaform-livid.vercel.app` with a stale `DATABASE_URL` and
  no `API_KEY` **until the project is removed** — it is legacy, not a target.

## Owner-side follow-up (human, outside the repo)

- [ ] **Delete (or suspend) the Vercel project** in the dashboard — this is the
  actual retirement step; the repo side (workflow removal) is done. Deleting it
  also closes the "which string lives in Vercel" rotation-hygiene question and
  the publicly-reachable surface behind tracker items #1/#5.
- [ ] (Optional, once the project is gone) remove the `VERCEL_TOKEN` /
  `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID` repo secrets and the scope grant.

## If hosted preview is ever wanted again

Record it as a fresh stage, don't resurrect this doc: a new deploy workflow (or
dashboard deploys), env vars with rotated values, the §2-era routing question
answered, and session auth (C1B) enabled before any wider exposure.

## User-side hygiene (owner: human — not automatable, D3)

- [ ] **Safari history scrub** — past sessions may hold URLs/queries that
  shouldn't linger on a shared machine.
- [ ] **Thread-history deletion decision** — agent chat threads persist
  server-side (retained until a deletion request completes). Decide
  whether/what to request deletion for; requests go through support (CCPA
  45-day / GDPR 1-month clocks). See
  `docs/security-remediation-tracker.md`.
