# AGENTS.md — Replicant Brief

*The boot file for a fresh agent session (or new human) on this repo + this Mac. Read this first; it encodes what cannot be derived from the code. Live state lives in the oracles (below) — never trust prose over them.*

**Baseline test**: after macOS updates, reboots, or anything traumatic, run `scripts/baseline --verify`. Hard drift (services, mode, nodes, git branch) = investigate before proceeding; soft drift (manual API up/down, dirty-file count) is reported but allowed. After an *intentional* change, re-snapshot (`--snapshot`) and commit the new baseline.

## Recovery drill (first 5 commands)

```bash
bash scripts/next        # computed to-do list + blockers (read-only)
bash scripts/dev --check # config/secret mode (env-only vs bws), Python, port
bash scripts/baseline --verify # am I still me? (drift check vs committed baseline; exit 1 = investigate)
git status -sb && git log --oneline -5
curl -s -m 3 http://localhost:8000/api/health   # local API (may be down — manual launch, see below)
```
Then read: `SETUP.md` (network/share/accounts), `docs/security-remediation-tracker.md` (security state), `docs/DEVELOPMENT_PLAN.md` (roadmap). You are now operational.

## Who's who

- **Dalton Lewis** — this Mac's owner. Tailnet identity `malgrath@gmail.com`. GitHub pushes to `dev-dalton`.
- **Jay** (file-share partner, `jayswin143@gmail.com`, Mac `justins-macbook-pro`) — very likely the same person as **Justin Swinney** (`swinneyj`, repo owner, coworker: Splunk/Vercel/BWS admin). *Confirm once, then fix this line.*
- Communication: casual chat is fine; credentials are not (see protocols).

## Machine quirks — Dalton's MacBook (macOS 26) — hard-won, do not re-learn

- **Elevation tool**: never put `sudo` inside an elevation command (the tool adds privilege itself). Prefix with `cd /tmp &&` — the elevated shell can't resolve the project cwd. Expect `shell-init: getcwd` noise and occasional exit-code lies; verify effects, don't trust the error text.
- **Account creation: GUI only.** Terminal-created accounts (`sysadminctl`/`dscl`) get a broken password-server record → phantom "PAM: user account has expired" on SSH, silent SMB auth failure. GUI-created accounts get SecureToken automatically and work. (Saga: `remoteguest` is GUI-made, token-enabled, key staged — and SSH still fails pre-auth with no diagnostic. macOS bug. Retest after every macOS point update.)
- **Trackpad "Natural scrolling" label is inverted on this machine.** Correct state: Natural **ON** (scrolls traditionally). Don't "fix" it.
- **`/docs` 404 on the local API is expected** — gated behind `ENABLE_DOCS` (security hardening, not a bug).
- **launchd API service is retired** — the API starts manually via `scripts/dev` (port 8000). Boot persistence deliberately not restored yet.
- **`timeout` and `setsid` don't exist on macOS.** To daemonize: Python double-fork + `os.setsid()`. Plain `nohup … & disown` dies when the tool's shell session tears down.

## Infrastructure map

| Thing | Where | Notes |
|---|---|---|
| Tailscale | `tailscaled` LaunchDaemon (`sh.brew.tailscale`), auto-starts | Dalton `100.84.93.19`; Jay `100.88.143.23`. CLI: `/opt/homebrew/bin/tailscale`. GUI app deleted on purpose — don't reinstall (mints duplicate nodes) |
| File share | `/Users/Shared/Exchange` (symlink on Dalton's Desktop) | SMB "Exchange", **guest access on**, port 445. Jay mounts `smb://100.84.93.19` as Guest. Taildrop can't work cross-user — this is why SMB exists |
| Secrets | BWS org **SSC-Lewis** → project **SOC Platform** → machine account **Machine-Dev** (scoped to project only) | 4 secrets: `DATABASE_URL` (local Postgres until Neon URL arrives), `OLLAMA_URL`, `API_KEY` (dormant), `CORS_ORIGINS`. `.env` is a bootstrap: token + local-only keys, 0600 |
| Local API | `scripts/dev` → port 8000 | `scripts/pull-secrets` regenerates `.env` from vault (key names only, never values) |
| Vercel prod | `https://soc-plaform-livid.vercel.app` | **All branches deploy `--prod` to this one domain** — last push wins. Smoke test runs on every deploy |

## Standing protocols

1. **Secrets**: long-lived/vault credentials NEVER transit chat — land them privately, in `.env` or the BWS UI. Disposable/short-lived passwords (a guest account being debugged) may appear in chat when necessary. The BWS token itself: user types it, agent never touches it.
2. **The Neon URL travels by call, Signal, or BWS — never email/chat.** This single rule exists because a credential leak in this chat's history caused a rotation incident.
3. **Git**: branch `dev-dalton`; conventional-commit subjects; Codebuff footer via `git commit -F <file>` (heredoc quoting breaks in this shell). Push only when asked. Never touch files untracked by you without asking.
4. **macOS config changes**: verify effects after acting — elevated commands here have a history of "exit 0 but didn't happen" and "errored but did happen."

## Non-derivable history (the "why" memories)

- **The credential incident**: a Neon password was once pasted into a chat and leaked → rotation → the entire BWS workflow exists so it can't recur. Never let a long-lived secret into a transcript again.
- **The `.python-version` outage**: one pinning file silently broke 8 Vercel deploys (14:03 success → 14:11 failure boundary). Lesson: probe deploys after pushing; a "verify" check that runs *between* builds lies to you. The deploy smoke test exists because of this.
- **The duplicate smoke tests**: two sessions shipped competing `deploy.yml` smoke tests (main + dev-dalton) that collided at merge. Lesson: **check Justin's existing work before building CI anything.**
- **The SecureToken saga**: hours lost to terminal-created accounts before the GUI-only rule. Applies on Jay's Mac too if accounts get created there.

## Open threads (owners marked)

- ⏳ **Neon URL** (Justin → Dalton): edit `DATABASE_URL` in BWS → `scripts/pull-secrets` → restart; **also Vercel env vars**
- ⏳ **API gate activation** (decision: Dalton+Justin): `API_KEY` already in vault; one Vercel env var flips it on
- 🔧 **CI pytest-on-push** (Dalton, after checking Justin's CI): only `deploy.yml` exists; the 124-test suite doesn't run in CI yet
- 🔧 **Phases 3–5** (roadmap in `docs/DEVELOPMENT_PLAN.md`): real Splunk REST (mock backend ready), judgment-flow audit, ops backlog (backups, retention, auth)
- 🔧 **remoteguest SSH** (waiting on macOS): fully staged — retest `ssh remoteguest@100.84.93.19` after OS updates; rotate its `12345` password before real use
- 🔧 **Boot persistence** (decision): point launchd at `scripts/dev` for vault-injected auto-start, or keep manual launches
- ⚪ **`close_freebuff_tabs.py`** (Dalton): personal utility, untracked — commit to `Tools/`, keep untracked, or delete

## Doc index

`SETUP.md` — network/share/accounts/BWS state · `RUNBOOK.md` — restore procedures (incl. archived `splunk-es-backup-toolkit/triage.db`) · `docs/DEVELOPMENT_PLAN.md` — Phases 1–5 · `docs/security-remediation-tracker.md` — vuln log + decisions · `.env.bws.template` — BWS naming contract · `CONTAINERIZATION.md` — Docker/deploy notes
