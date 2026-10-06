# Security Policy

**Repo:** `swinneyj/soc-plaform` (public) · **Owner:** Dalton Lewis

This repository implements a local-first SOC triage and investigation workspace. If
you find a security issue in this code, **please report it privately** — do not open
a public issue.

## Preferred reporting channel

**Email:** `dalton` at the project's contact address (see below).

In your report, include:
- What you found (the behavior, not just the file)
- The simplest reproducible steps you have
- Any information that would help assess impact (endpoint, auth state assumed,
  data sensitivity)

Please do **not** open a public GitHub issue for a security finding until the owner
has had a chance to assess and respond.

## What "security issue" means here

This project is a security-adjacent tool (it ingests Splunk ES notable data and
stores investigation state), so treat anything that could:
- exfiltrate or mutate case/notable/evidence data,
- execute code or spawn processes with unexpected inputs,
- leak credentials, tokens, or internal error detail,
- bypass the API auth gate or the Splunk boundary latch

...as security-relevant, even if the exposure is currently localhost/Tailscale only.

## Current posture (summary — see tracker for full detail)

- Auth: API-key gate on mutating `/api/*` routes (armed via `API_KEY`); session
  auth + CSRF + analyst/admin roles built behind `AUTH_MODE=session` (flag off
  by default). Reads and `/api/health` stay open in API-key mode by contract.
- Secrets: not tracked in git; credential-bearing keys live in the macOS Keychain
  / BWS vault, injected at launch (`scripts/start`, `scripts/dev`).
- Data: one shared live Neon Postgres backend. Test rows and destructive DB
  actions (wipe, batch-delete, case deletes) hit **real shared data** — confirm
  which DB you are on and get explicit confirmation before any destructive action.
- Ingestion: Splunk-boundary "latch" (`services/splunk_boundary.py`) is the
  single choke point; quarantined mode is the default.
- Error handling: 500 responses return a generic detail + server-side request-id
  log; route-level `str(e)` 500 leaks are swept.

## Scope and limits

- This project is **public**. `SECURITY.md` is the disclosure channel for the
  repo; it does **not** cover vulnerabilities in third-party services the project
  depends on (Ollama, PostgreSQL, Splunk ES, Tailscale) — those have their own
  reporting processes.
- Freebuff Desktop is the agent harness used on this repo. Its own open security
  issues and the operating rules for agent sessions live in `AGENTS.md` →
  "Freebuff Desktop risk notes." That section is the source of truth for those
  notes; this file does not re-litigate them.

## Want the detailed remediation log?

See `docs/security-remediation-tracker.md` for the prioritized finding log,
architecture-hardening notes (Splunk boundary / latch), and the open-items list.
That file is the detailed record; this file is the public on-ramp for reports.

## Author contact

Owner: Dalton Lewis (Tailnet identity `malgrath@gmail.com`).
Repo owner/coworker: Justin Swinney (`swinneyj`).

Reports are welcome in English or plain text; a short reproducible description is
worth more than a long one.
