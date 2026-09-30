# Vercel Handoff Package (D1) — RETIRED

*Assembled Sept 30, 2026 (execution-playbook stage D1). Retired the same day,
by owner decision: the hosted Vercel deployment is not needed — the platform
runs fully locally (`scripts/start`, mock-Splunk default) and the stated goal
(local fake-Splunk testing → eventual real-Splunk import) never leaves this
machine. A second same-day owner decision keeps the Vercel project itself
**on ice** — frozen, undeployable, retained in case circumstances change.
The env-var handoff, routing question, and deploy checklist below are all moot
without a deploy.*

## What was retired (Sept 30, 2026)

- **`.github/workflows/deploy.yml` deleted.** Pushes to `main` / `dev` /
  `dev-dalton` / `dev-justin` no longer deploy anywhere; the last-push-wins
  `--prod` deploy and its `/api/health` smoke test are gone with it.
- **§1 env vars, §2 routing question, §5 checklist — moot.** No deploy means no
  `DATABASE_URL` / `API_KEY` / `CORS_ORIGINS` / `ENABLE_DOCS` to set in Vercel
  and no CDN-vs-function injection decision to make.
- **Status of the running deployment:** the last-built (pre-rotation) bundle
  still serves `soc-plaform-livid.vercel.app` with a stale `DATABASE_URL` and
  no `API_KEY` — deliberately kept on ice (see below); legacy, not a target.

## Owner decision (Sept 30, 2026): keep the project on ice

Deliberately **not** deleting the project — keep it in case circumstances
change. Effect of that choice:

- The repo can never deploy to it again (`deploy.yml` is gone), so it stays
  frozen on its last build (pre-rotation `DATABASE_URL`, no `API_KEY`).
- It keeps publicly serving that stale bundle at `soc-plaform-livid.vercel.app`
  — treat as legacy: don't point anything sensitive at it, don't put new
  secrets in its env.
- Optional cleanup whenever desired (no urgency): delete the project in the
  Vercel dashboard — closes the "which string lives in Vercel" hygiene
  question and the publicly-reachable surface behind tracker items #1/#5 —
  then remove the `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID` repo
  secrets and the scope grant.

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
