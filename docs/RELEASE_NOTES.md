# Release Notes — dev-dalton (pre-merge draft)

*Living draft — stage E.3 of `docs/EXECUTION_PLAYBOOK.md`. Finalized when
stage C lands; the `main` merge itself is human-executed (E.4).*

## What landed (by stage)

### Stage A — Security floor (P0) ✅
- **A1** `0e34724` — destructive actions now require the armed API key: GET
  delete wrappers removed, `GET /api/db/triage` delete side-effects stripped,
  frontend moved to POST delete, path-aware mutation gate, and the
  `TestRouteSemanticsAudit` route-allowlist lock (negative control proven).
- **A2** `4ba1391` + `6dd1fa3` — dependencies pinned and pruned (redis,
  python-pptx out; cryptography kept for the live `pki_cert_decoder` tool),
  dual-runtime `python_version` marker pins, pip-audit clean on the
  production branch, Dependabot config added.
- **A3** `dcc7551` — tracker + backend-plan statuses synced to reality.

### Stage B — Repo hygiene (P1) ✅
- `2288d2b` root one-shots → attic · `475b252` debris sweep, single README ·
  `c8b28ed` Tools staging copy → attic · `bd539b7` deployment-story matrix ·
  `638c73b` `.gitignore` BOM strip.

### Stage C — Product work (P2) 🚧
- **C1** plan `7281fe8` (signed off). **C1B.1** `30eae76` — users + sessions
  models, scrypt helpers, `scripts/manage_users.py` CLI (inert until
  `AUTH_MODE=session`; verified 282×2 + CI green).
- Remaining: C1B.2–C1B.4 (auth routes + CSRF + UI gate + matrix), C2
  (hardening limits — defaults awaiting sign-off), C3 (per-stage models),
  C4 (paste-box boundary batches). *Updated here when they land.*

### Stage D — Externals & wait-states (P3) ✅ (docs assembled)
- **D1** `3d614cc` — [Vercel handoff](VERCEL_HANDOFF.md): env vars, routing
  question, deploy pipeline, break table, owner checklist.
- **D2** `4032b23` — [Splunk rehearsal](SPLUNK_REHEARSAL.md): five-step
  live-connector checklist, credential-delivery rule included.
- **D3** — user-side hygiene folded into the handoff doc §6.

## Final gate counts
*(updated at E time)* — currently: pytest **282 × 2** (3.14 + 3.9),
undefined-names OK, merge-markers OK, baseline holding, `node --check` clean,
23 UI harness scenarios green.

## Known-open items (recorded, not blocking)
- **Vercel env vars + routing answer** (Justin) — `docs/VERCEL_HANDOFF.md` §5.
- **Dependabot vulnerability-alerts toggle** — needs repo admin
  (Settings → Code security); `dependabot.yml` itself is live.
- **3.9 accepted-risk pins** — starlette 0.49.3 / pytest 8.4.2 have advisories
  with no 3.9-compatible fix; retires with the 3.9 CI leg (tracker #9).
- **Live Splunk rehearsal** — blocked on `SPLUNK_URL`/`SPLUNK_TOKEN`
  (`docs/SPLUNK_REHEARSAL.md`).
- **D3 user-side hygiene** — Safari scrub, thread-deletion decision.

## Ops entry points
- `scripts/start` — one-command start; rides backup (20 h staleness skip) +
  retention dry-run on every start; `scripts/start --backup` forces a dump.
- Secrets: BWS vault + macOS Keychain (`scripts/secrets-keychain`); `.env`
  regenerated via `scripts/pull-secrets`. Never in Git, never in chat.
- Deploy: push to a deploy branch → `deploy.yml` → prod (last push wins);
  smoke = `GET /api/health` → `{"status": "healthy", …}`.

## Merge checklist (human executes, E.4)
- [ ] CI green on `dev-dalton`, clean tree, all stages ✅ above
- [ ] `git merge --no-ff dev-dalton` on `main`
- [ ] Tag the release
- [ ] Deploy per `docs/VERCEL_HANDOFF.md`; smoke + one authenticated UI write
