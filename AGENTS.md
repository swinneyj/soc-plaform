# AGENTS.md — Replicant Brief

*The boot file for a fresh agent session (or new human) on this repo + this Mac. Read this first; it encodes what cannot be derived from the code. Live state lives in the oracles (below) — never trust prose over them.*

**Baseline test**: after macOS updates, reboots, or anything traumatic, run `scripts/baseline --verify`. Hard drift (services, mode, nodes, git branch) = investigate before proceeding; soft drift (manual API up/down, dirty-file count) is reported but allowed. After an *intentional* change, re-snapshot (`--snapshot`) and commit the new baseline.

**Provenance system**: every memory below is tagged — `[V]` **VERIFIED** (witnessed live this machine, or re-derivable by running the oracles), `[I]` **INHERITED** (from prior session transcripts; plausible and consistent but not re-witnessed here), `[A]` **ASSUMED** (inference; flagged with what would confirm/deny it). Treat `[A]` as a question, not a fact. When you personally witness something contradicting a tag, downgrade/upgrade it **in the same commit** as the change.

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

- **Dalton Lewis** — this Mac's owner. Tailnet identity `malgrath@gmail.com`. GitHub pushes to `dev-dalton`. `[V]` (witnessed: pushes, tailnet status, shell whoami)
- **Jay** (file-share partner, `jayswin143@gmail.com`, Mac `justins-macbook-pro`) — likely the same person as **Justin Swinney** (`swinneyj`, repo owner, coworker: Splunk/Vercel/BWS admin). `[A]` — evidence: same first-name register ("Jay"/Justin), shares-with-me arrived the same week as his repo pushes, macOS + Mac workflow familiarity. **Confirm with one sentence to either party, then rewrite this line as `[V]`.** If they are *different people*, the secrets/git protocols need re-review (a third party may be implied).
- Communication: casual chat is fine; credentials are not (see protocols). `[V]`

## Machine quirks — Dalton's MacBook (macOS 26) — hard-won, do not re-learn

- **Elevation tool**: never put `sudo` inside an elevation command (the tool adds privilege itself). Prefix with `cd /tmp &&` — the elevated shell can't resolve the project cwd. Expect `shell-init: getcwd` noise and occasional exit-code lies; verify effects, don't trust the error text. `[V]` (hit 5+ times live, incl. a false "success" and a false failure)
- **Account creation: GUI only.** Terminal-created accounts (`sysadminctl`/`dscl`) get a broken password-server record → phantom "PAM: user account has expired" on SSH, silent SMB auth failure. GUI-created accounts get SecureToken automatically and work. `[V]` (both halves witnessed: headless accounts failed repeatedly; GUI-made `remoteguest` got a token instantly) — *with an open residual*: the GUI account **still** fails SSH with a bare pre-auth close and no diagnostic. See `remoteguest` thread below.
- **Trackpad "Natural scrolling" label is inverted on this machine.** Correct state: Natural **ON** (scrolls traditionally). Don't "fix" it. `[V]` (user flip test: toggle ON = traditional feel; survived reboot; all prefs read "off" while behaving "on")
- **`/docs` 404 on the local API is expected** — gated behind `ENABLE_DOCS` (security hardening, not a bug). `[V]`
- **launchd API service is retired** — the API starts manually via `scripts/dev` (port 8000). Boot persistence deliberately not restored yet. `[V]` (port empty at audit; old service absent from `who`/process list)
- **`timeout` and `setsid` don't exist on macOS.** To daemonize: Python double-fork + `os.setsid()`. Plain `nohup … & disown` dies when the tool's shell session tears down. `[V]` (both failure modes witnessed; double-fork launch survived shell exit)
- **`brew services` shows status `none` for roots daemons it didn't start this session** — check `/Library/LaunchDaemons/` + `launchctl print` instead. `[V]`

## Infrastructure map

| Thing | Where | Notes |
|---|---|---|
| Tailscale `[V]` | `tailscaled` LaunchDaemon (`sh.brew.tailscale`), auto-starts | Dalton `100.84.93.19`; Jay `100.88.143.23`. CLI: `/opt/homebrew/bin/tailscale`. GUI app deleted on purpose — don't reinstall (mints duplicate nodes) `[V]` (duplicate node observed & deleted) |
| File share `[V]` | `/Users/Shared/Exchange` (symlink on Dalton's Desktop) | SMB "Exchange", **guest access on**, port 445. Jay mounts `smb://100.84.93.19` as Guest. Taildrop can't work cross-user `[V]` (docs-confirmed; guest write+read verified live) |
| Secrets `[V]` | BWS org **SSC-Lewis** → project **SOC Platform** → machine account **Machine-Dev** (scoped to project only) | 4 secrets: `DATABASE_URL` (local Postgres until Neon URL arrives), `OLLAMA_URL`, `API_KEY` (dormant), `CORS_ORIGINS`. `.env` is a bootstrap: token + local-only keys, 0600. BWS values verified equal to old local values via silent compare |
| Local API `[V]` | `scripts/dev` → port 8000 | `scripts/pull-secrets` regenerates `.env` from vault (key names only, never values) |
| Vercel prod `[I]` | `https://soc-plaform-livid.vercel.app` | **All branches deploy `--prod` to this one domain** — last push wins. Smoke test runs on every deploy. (Inherited from deploy-thread transcripts; re-verify with one `curl` + `gh run list` when it matters) |

## Standing protocols

1. **Secrets**: long-lived/vault credentials NEVER transit chat — land them privately, in `.env` or the BWS UI. Disposable/short-lived passwords (a guest account being debugged) may appear in chat when necessary. The BWS token itself: user types it, agent never touches it. `[V]` (protocol born from the incident below; enforced all session)
2. **The Neon URL travels by call, Signal, or BWS — never email/chat.** `[I]` (incident inherited from prior thread transcripts; rule consistent with protocol 1) This single rule exists because a credential leak in a chat's history caused a rotation incident.
3. **Git**: branch `dev-dalton`; conventional-commit subjects; Codebuff footer via `git commit -F <file>` (heredoc quoting breaks in this shell `[V]`). Push only when asked. Never touch files untracked by you without asking. `[V]`
4. **macOS config changes**: verify effects after acting — elevated commands here have a history of "exit 0 but didn't happen" and "errored but did happen." `[V]`
5. **Check Justin's existing work before building CI anything.** `[I]` (born from the duplicate smoke-test collision; re-derived below)

## Non-derivable history (the "why" memories)

- **The credential incident** `[I]`: a Neon password was once pasted into a chat and leaked → rotation → the entire BWS workflow exists so it can't recur. (Inherited from prior transcripts; consistent with all current protocol design. The BWS org itself is `[V]` — the incident story is the inherited *why*.)
- **The `.python-version` outage** `[I]`: one pinning file silently broke 8 Vercel deploys (14:03 success → 14:11 failure boundary). Lesson: probe deploys after pushing; a "verify" check that runs *between* builds lies to you. The deploy smoke test exists because of this.
- **The duplicate smoke tests** `[I]`: two sessions shipped competing `deploy.yml` smoke tests (main + dev-dalton) that collided at merge. Lesson: check Justin's existing work before building CI anything.
- **The SecureToken saga** `[V]`: witnessed live this session (the failures, the GUI fix, the residual SSH bug). Applies on Jay's Mac too if accounts get created there. `[I]` extension
- **The self-dial lesson** `[V]`: you cannot test Tailscale SSH against your own tailnet IP from the same Mac (loopback short-circuits into OS sshd — Tailscale server never sees it). Test cross-machine, or loopback for regular sshd.

## Open threads (owners marked)

- ⏳ **Neon URL** (Justin → Dalton) `[I — waiting]`: edit `DATABASE_URL` in BWS → `scripts/pull-secrets` → restart; **also Vercel env vars**
- ⏳ **API gate activation** (decision: Dalton+Justin) `[I — waiting]`: `API_KEY` already in vault; one Vercel env var flips it on
- 🔧 **CI pytest-on-push** (Dalton, after checking Justin's CI) `[V — only deploy.yml exists locally]`: the 124-test suite doesn't run in CI yet
- 🔧 **Phases 3–5** (roadmap in `docs/DEVELOPMENT_PLAN.md`) `[I]`: real Splunk REST (mock backend ready), judgment-flow audit, ops backlog (backups, retention, auth)
- 🔧 **remoteguest SSH** `[V — staged, failing with no diagnostic]`: token-enabled, key installed, perms correct, password valid — SSH still closes pre-auth. macOS bug hypothesis `[A]`. Retest after OS updates; rotate its `12345` password before real use
- 🔧 **Boot persistence** (decision) `[V — currently manual]`: point launchd at `scripts/dev` for vault-injected auto-start, or keep manual launches
- ⚪ **`close_freebuff_tabs.py`** (Dalton) `[V — untracked personal utility]`: commit to `Tools/`, keep untracked, or delete

## Doc index

`SETUP.md` — network/share/accounts/BWS state `[V]` · `RUNBOOK.md` — restore procedures (incl. archived `splunk-es-backup-toolkit/triage.db`) `[V]` (archived this session, integrity-checked) · `docs/DEVELOPMENT_PLAN.md` — Phases 1–5 `[I]` · `docs/security-remediation-tracker.md` — vuln log + decisions `[V]` (committed this session) · `.env.bws.template` — BWS naming contract `[V]` · `CONTAINERIZATION.md` — Docker/deploy notes `[I]`
