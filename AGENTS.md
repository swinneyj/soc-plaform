# AGENTS.md — Replicant Brief

*The boot file for a fresh agent session (or new human) on this repo + this Mac. Read this first; it encodes what cannot be derived from the code. Live state lives in the oracles (below) — never trust prose over them.*

**Baseline test**: after macOS updates, reboots, or anything traumatic, run `scripts/baseline --verify`. Hard drift (services, mode, nodes, git branch) = investigate before proceeding; soft drift (manual API up/down, dirty-file count) is reported but allowed. After an *intentional* change, re-snapshot (`--snapshot`) and commit the new baseline.

**Provenance system**: every memory below is tagged — `[V]` **VERIFIED** (witnessed live this machine, or re-derivable by running the oracles), `[I]` **INHERITED** (from prior session transcripts; plausible and consistent but not re-witnessed here), `[A]` **ASSUMED** (inference; flagged with what would confirm/deny it). Treat `[A]` as a question, not a fact. When you personally witness something contradicting a tag, downgrade/upgrade it **in the same commit** as the change.

## Recovery drill (first 5 commands)

```bash
bash scripts/next        # computed to-do list + blockers (read-only)
bash scripts/start --check # status of every piece (ollama/secrets/db/api) — read-only
bash scripts/start       # ONE-COMMAND START: ollama + BWS preflight + daemonized API + health
bash scripts/baseline --verify # am I still me? (drift check vs committed baseline; exit 1 = investigate)
git status -sb && git log --oneline -5
curl -s -m 3 http://localhost:8000/api/health   # local API (down = launch via scripts/start)
```

Desktop icon: `SOC Platform.command` (Desktop + ~/Applications) — double-click = scripts/start + dashboard, survives shell teardown via the daemonized launcher. Repo copy: `scripts/soc-platform.command` (resolves the checkout from its own location).
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
- **Stale `postmaster.pid` after unclean shutdown** (Homebrew `postgresql@16`, port 5432): Postgres dying at reboot leaves its lock behind; after restart, the lock's PID can be **reused by an unrelated process** (witnessed: PID 635 = a Photos widget), so the log spams `FATAL: lock file "postmaster.pid" already exists` and the port stays dead. **`brew services start` lies through this** — reports `Bootstrap failed: 5` / exit 5 while the server actually comes up fine; trust the log tail (`database system is ready to accept connections`) and `launchctl print` (`state = running`), not the exit code. **Fix, in order**: (1) `ps -p <lock-PID>` + `pgrep -fl postgres` to confirm **no** postgres process exists, (2) only then `rm /opt/homebrew/var/postgresql@16/postmaster.pid`, (3) `brew services start postgresql@16`, (4) verify with `pg_isready -h localhost -p 5432` + log tail. Do **not** delete the lock while any postgres process lives. `[V]` (full cycle witnessed 2026-09-28, incl. the brew exit-code lie and clean WAL recovery after lock removal)

## Infrastructure map

| Thing | Where | Notes |
|---|---|---|
| Tailscale `[V]` | `tailscaled` LaunchDaemon (`sh.brew.tailscale`), auto-starts | Dalton `100.84.93.19`; Jay `100.88.143.23`. CLI: `/opt/homebrew/bin/tailscale`. GUI app deleted on purpose — don't reinstall (mints duplicate nodes) `[V]` (duplicate node observed & deleted) |
| File share `[V]` | `/Users/Shared/Exchange` (symlink on Dalton's Desktop) | SMB "Exchange", **guest access on**, port 445. Jay mounts `smb://100.84.93.19` as Guest. Taildrop can't work cross-user `[V]` (docs-confirmed; guest write+read verified live) |
| Secrets `[V]` | BWS org **SSC-Lewis** → project **SOC Platform** → machine account **Machine-Dev** (scoped to project only) | 4 secrets: `DATABASE_URL` (Neon since Sept 28), `OLLAMA_URL`, `API_KEY` (dormant), `CORS_ORIGINS`. `.env` is a bootstrap: token + local-only keys + `# NAME=@keychain` markers, 0600. **Credential-bearing keys (`DATABASE_URL`, `API_KEY`) live in the macOS login Keychain** (service `soc-platform`, `scripts/secrets-keychain` get/put/inject/migrate) — `scripts/start`/`scripts/dev` inject them; explicit env wins; BWS-injected values win at launch |
| Local API `[V]` | `scripts/dev` → port 8000 | `scripts/pull-secrets` regenerates `.env` from vault (key names only, never values) |
| Vercel prod `[I]` | `https://soc-plaform-livid.vercel.app` | **All branches deploy `--prod` to this one domain** — last push wins. Smoke test runs on every deploy. (Inherited from deploy-thread transcripts; re-verify with one `curl` + `gh run list` when it matters) |

## Standing protocols

1. **Secrets**: long-lived/vault credentials NEVER transit chat — land them privately, in `.env` or the BWS UI. Disposable/short-lived passwords (a guest account being debugged) may appear in chat when necessary. The BWS token itself: user types it, agent never touches it. `[V]` (protocol born from the incident below; enforced all session)
2. **The Neon URL travels by call, Signal, or BWS — never email/chat.** `[I]` (incident inherited from prior thread transcripts; rule consistent with protocol 1) This single rule exists because a credential leak in a chat's history caused a rotation incident.
3. **Git**: branch `dev-dalton`; conventional-commit subjects; Codebuff footer via `git commit -F <file>` (heredoc quoting breaks in this shell `[V]`). Push only when asked. Never touch files untracked by you without asking. `[V]`
4. **macOS config changes**: verify effects after acting — elevated commands here have a history of "exit 0 but didn't happen" and "errored but did happen." `[V]`
5. **Check Justin's existing work before building CI anything.** `[I]` (born from the duplicate smoke-test collision; re-derived below)

## Freebuff Desktop risk notes

*Freebuff Desktop (`com.freebuff.desktop`, Electron) is the agent harness in use on this repo. Vendor: Freebuff, Inc. (YC F24, ~4 people, San Francisco) — ad-funded, ~$500K raised. Public tracker checked 2026-09-28; re-check before trusting.* `[V]` (public URLs below re-derivable)

**Data handling (from their privacy policy + README, 2026-09-25/28):** chat threads are stored **server-side indefinitely** — retained until a deletion request completes; no automatic expiry. Prompts/messages may be analyzed to personalize ads. Device fingerprinting is part of auth (`~/.config/manicode/credentials.json` stores `fingerprintId` + `fingerprintHash`, per issue #947). Ads are injected into model responses (`common/src/util/lazy-response-ads.ts`, per PR #1243). AI training only where a model/feature is labeled for it. Deletion requests: support@codebuff.com (CCPA 45d / GDPR 1mo clocks).

**Open security issues in the public tracker (none shipped fixed as of 2026-09-28):**

- **#1146 — out-of-scope `rm -rf` deletion**: agent deleted files outside the project on Windows/Git Bash, permanently (no Recycle Bin), no confirmation gate. Shell-generic failure mode; applies here.
- **#1231 — XSS in `escapeString`** (`common/src/util/string.ts`): doesn't escape `< > & '`; fix PR unmerged (`pr:needs-work`). Electron renderer XSS is escalation-class.
- **#1306 — MCP tool parameters arrive as `{}`** (~100% repro, 2 models, still broken in 0.0.172; fix #1259 unshipped): any MCP connector is broken AND unsupervised.
- Public repo lags the shipped app: fixes are labeled `pr:port-candidate` — "worth porting into the private source tree." Auditability is partial.

**Operating rules (binding for agent sessions on this repo):**

1. **Keep the tree committed.** Git is the undo for agent mistakes; origin is the off-machine copy. Working-tree drift = elevated risk window.
2. **Review every destructive terminal command before confirming.** `rm -rf`, anything outside the repo path, anything touching `~/` beyond this checkout = hard stop, ask the user.
3. **No MCP connectors in Desktop** until #1306 ships fixed.
4. **Never select the Space Bunny Alpha model** — anonymous provider that retains prompts, per Freebuff's own README. Prefer the unmetered open-weight models (GLM 5.3 Flash, DeepSeek V4.1 Flash).
5. **No credentials or secrets in prompts or thread content** — extension of protocol 1/2: threads persist server-side on an ad-funded platform, so treat every prompt as retained-until-deleted and ad-profiled.
6. For sensitive work, prefer routing locally installed Claude Code/Codex agents through Desktop (own provider accounts → own provider's data terms) over Freebuff's included catalog.

## Non-derivable history (the "why" memories)

- **The credential incident** `[I]`: a Neon password was once pasted into a chat and leaked → rotation → the entire BWS workflow exists so it can't recur. (Inherited from prior transcripts; consistent with all current protocol design. The BWS org itself is `[V]` — the incident story is the inherited *why*.)
- **The `.python-version` outage** `[I]`: one pinning file silently broke 8 Vercel deploys (14:03 success → 14:11 failure boundary). Lesson: probe deploys after pushing; a "verify" check that runs *between* builds lies to you. The deploy smoke test exists because of this.
- **The duplicate smoke tests** `[I]`: two sessions shipped competing `deploy.yml` smoke tests (main + dev-dalton) that collided at merge. Lesson: check Justin's existing work before building CI anything.
- **The SecureToken saga** `[V]`: witnessed live this session (the failures, the GUI fix, the residual SSH bug). Applies on Jay's Mac too if accounts get created there. `[I]` extension
- **The self-dial lesson** `[V]`: you cannot test Tailscale SSH against your own tailnet IP from the same Mac (loopback short-circuits into OS sshd — Tailscale server never sees it). Test cross-machine, or loopback for regular sshd.

## Open threads (owners marked)

- ✅ **Neon URL** `[V — cutover done Sept 28, local verified]`: fresh console-copied URL in BWS → `pull-secrets` → API verified live against Neon (read + write roundtrip). Stale-URL first pull failed auth — connection strings come from the console copy button only, never prior messages (second example of protocol 2's why). Prod (Vercel) probed same day: **also Neon-backed, same 7 rows** — Justin's side evidently already works; remaining is a one-line confirmation of WHICH string lives in Vercel (rotation hygiene). First stale-URL detail + no-other-copies sweep in tracker item #1
- ⚠️ **One shared production dataset** `[V]`: local API, prod Vercel, and Justin's instance all read/write the SAME Neon database. Consequences:
  - `wipe_db.py`, the delete-all/batch-delete evidence endpoints, case deletes, and `Restore_SOC_From_Handoff.bat`-style dump restores hit **real shared data**, not a sandbox
  - Test rows created locally appear on prod immediately — use clearly-marked titles (e.g. the `NEON WRITE-PATH SMOKE TEST` + `writepath_smoketest` source_system pattern) and delete them in the same session
  - The Splunk-boundary batch purge is the sanctioned undo for ingest mistakes; nothing analogous exists for wipe_db — it has no undo
  - Before ANY destructive DB action: `scripts/start --check`, confirm which DB you're on (neon.tech in DATABASE_URL = shared prod), and get explicit user confirmation
- ⏳ **API gate activation** (decision: Dalton+Justin) `[I — waiting]`: `API_KEY` already in vault; one Vercel env var flips it on
- ✅ **CI pytest-on-push** `[V — live since Sept 28]`: `.github/workflows/tests.yml` runs the 124-test suite on every push/PR, Python 3.14 + 3.9 matrix. First runs green. Node20 deprecation annotations are cosmetic.
- 🔧 **Phases 3–5** (roadmap in `docs/DEVELOPMENT_PLAN.md`) `[I]`: real Splunk REST (mock backend ready), judgment-flow audit, ops backlog (backups, retention, auth)
- 🔧 **remoteguest SSH** `[V — staged, failing with no diagnostic]`: token-enabled, key installed, perms correct, password **rotated Sep 28, 2026** (old value scrubbed from all docs — never re-document passwords here; new value lives only in Dalton's password manager) — SSH still closes pre-auth. macOS bug hypothesis `[A]`. Retest after OS updates
- ✅ **Boot persistence** `[V — decided Sep 28, 2026: no auto-start]`: deliberate launcher only — `scripts/start` / the Desktop icon start everything on demand (`--stop` to end). With BWS landed, the `bws run` launcher pattern is confirmed; the retired LaunchAgent plist stays disabled on disk as a documented escape hatch (tracker E1). Revisit only if the icon step ever feels heavy
- ⚪ **`friend` account** `[V — fully deleted Sep 25]`: record + home gone. If anything ever recreates accounts by terminal, re-read the quirks section first

## Doc index

`SETUP.md` — network/share/accounts/BWS state `[V]` · `RUNBOOK.md` — restore procedures (incl. archived `splunk-es-backup-toolkit/triage.db`) `[V]` (archived this session, integrity-checked) · `docs/DEVELOPMENT_PLAN.md` — Phases 1–5 `[I]` · `docs/security-remediation-tracker.md` — vuln log + decisions `[V]` (committed this session) · `.env.bws.template` — BWS naming contract `[V]` · `CONTAINERIZATION.md` — Docker/deploy notes `[I]`
