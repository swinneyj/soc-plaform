# Local-first database policy

**Decision (2026-10-23): local Postgres is the runtime database. The shared Neon
project is an optional mirror.** The platform must run indefinitely without
touching a free-tier storage, compute-hour or egress quota, so no default launch
path may depend on Neon.

This replaces the September arrangement, where both this Mac and the coworker's
instance pointed at one Neon project and `DATABASE_URL` silently meant "Neon".

## Why

Neon Free is generous but metered: 1 GB per project (20 GB per account),
100 CU-hours per project per month, **5 GB egress per project per month**, and
compute suspends after 5 idle minutes with no way to disable that on Free. For a
log-triage platform the ceiling is row-heavy reads (egress), not storage, and a
single shared project means one dataset serving two people forever.

Local Postgres has no quota, no egress meter, wakes instantly, and its backup
story already exists (`scripts/backup_db.sh` → `local-backups/`). That is the
resilient shape for daily work; Neon stays as the thing you *push to* when
someone else needs to look, not the thing everything stands on.

## Resolution order (one implementation, two launchers)

`db/target.py` is the source of truth; `scripts/lib/db_target.sh` is the shell
shim both launchers source. First match wins:

| # | Condition | Result |
|---|---|---|
| 1 | `DATABASE_URL` set (shell or `.env`) | used as-is — **explicit operator intent always wins** |
| 2 | `SOC_DB_TARGET=shared` + `SHARED_DATABASE_URL` | the shared URL |
| 3 | `SOC_DB_TARGET=shared`, no shared URL | **refused** (exit 1) — never a silent local fallback |
| 4 | otherwise | local: `LOCAL_DATABASE_URL`, else `127.0.0.1:5432/soc_platform` |

Rule 3 is the load-bearing one. Falling back to local would leave two machines
writing to two datasets under the same name and nobody noticing; a hard refusal
is the only safe behaviour.

The same rule reaches **bare processes**: with `DATABASE_URL` unset, `db/models.py`
now asks `db.target`, so a one-off script, a hand-started uvicorn or
`scripts/manage_users.py` lands on the local primary (127.0.0.1:5432) and honours
`SOC_DB_TARGET`. It used to hardcode the compose host port 5433, which meant
those tools either failed against a dead port or hit a second database. Compose
still sets `DATABASE_URL` itself, so the exposed 5433 container port is
unchanged.

Beware the one thing that can still flip you to the shared side by accident:
wrapping a tool in `bws run` (it injects the vault's `DATABASE_URL`, which is the
Neon URL). That is exactly why the launchers only use BWS for the shared target.

Inspect the decision without launching anything:

```bash
.venv314/bin/python -m db.target --check     # target, source, host, live ping
scripts/dev --check                          # full launcher banner, starts nothing
scripts/start --check                        # every piece incl. DB target
```

## Launching

```bash
scripts/start                # local Postgres (default) — BWS deliberately NOT used
scripts/start --shared       # run against the shared Neon mirror instead
scripts/dev --shared         # uvicorn in the foreground, same target rules
```

Local mode skips `bws run` on purpose: the vault injects `DATABASE_URL` as an
environment variable, which would override the local target — that is exactly how
the old default became Neon. In local mode BWS supplies nothing the platform
needs, so `API_KEY` comes from the keychain instead
(`scripts/secrets-keychain inject`).

Shared mode uses BWS when `BWS_ACCESS_TOKEN` is present (it supplies the Neon URL
and the Splunk token) and falls back to `.env`/keychain otherwise. If the shared
URL is missing, the launcher stops with a named error rather than guessing.

## Moving data between the two

```bash
scripts/mirror_db pull                 # snapshot shared → local-backups/ (read-only)
scripts/mirror_db pull --replace --yes # snapshot, back up local, then truncate+load
scripts/mirror_db push                 # refused by design
```

* `pull` never writes to the shared side. It dumps with `--data-only` and
  `--no-owner`, and refuses to run if `pg_dump` is older than the server's major
  version (Neon is PG 18; `brew install postgresql@18` + `PGDMP=...` fixes it).
* `pull --replace` is the only destructive path. It insists on `--yes`, takes a
  fresh `local-backups/postgres_dump_*.sql.gz` first, refuses if the *local*
  target resolves to a Neon host, and truncates with `RESTART IDENTITY CASCADE`.
* `push` does not exist and should not be added. The shared dataset is written by
  its owner's flows, and the Splunk-boundary batch purge is its sanctioned undo —
  a bulk local→shared restore has no undo and would overwrite coworker data. If
  shared rows must change, do it through the API with a clearly marked test title
  and delete it in the same session (AGENTS.md, "One shared production dataset").

Snapshot now and restore later, without touching live data:

```bash
scripts/mirror_db pull --source "$SHARED_DATABASE_URL" --out /tmp/shared.sql.gz
gunzip -c /tmp/shared.sql.gz | psql "$DATABASE_URL"     # local only, your call
```

## Schema

`db/schema.py` runs on every local launch and is **additive only** —
`create_all()`, never a drop or an alter. That is what lets a local database that
predates a model (it lacked `users` and `auth_sessions`) start working without a
migration step.

It refuses the shared target unless `--allow-shared` is passed. Schema changes on
the Neon project are deliberate operations, not a side effect of "start the API
on my laptop".

```bash
.venv314/bin/python -m db.schema            # create missing tables (local)
.venv314/bin/python -m db.schema --check    # report drift only
```

## Backups

Both sets of backups are local files; nothing leaves the Mac:

* `scripts/backup_db.sh` — gzipped `pg_dump` into `local-backups/` (14 kept).
  `scripts/start` refreshes it when the newest dump is more than 20h old.
  It dumps whatever `DATABASE_URL` points at, so in `--shared` mode that dump is
  the shared mirror, not the primary — run `scripts/start --backup` in local mode
  to protect the primary.
* `scripts/mirror_db pull` — a shared-side snapshot in the same directory
  (`shared_snapshot_*.sql.gz`), so a bad mirror load is recoverable.

## Costs this protects against

| Piece | Free-tier limit that matters | Local-first answer |
|---|---|---|
| Neon | 1 GB/project, 100 CU-h, 5 GB egress/month, 5-min suspend | not on the default path at all |
| Neon (sharing) | one project serving two people | `--shared` on demand, `pull` to snapshot |
| Tailscale | Personal plan is homelab/personal only | unchanged — see AGENTS.md; decide deliberately if this is work use |
| BWS Secrets Manager | 2 users, 3 projects, 3 machine accounts | 1 project, 1 machine account — headroom |
| GitHub Actions | free for public repos; 500 MB artifacts, 10 GB cache | `ubuntu-latest`, no build artifacts archived |

The Neon ceiling is why this file exists. The Tailscale one is a licensing
question, not a technical one, and belongs to the owner.

## History

* Sept 28, 2026 — Neon cutover; local API and the coworker's instance shared one
  project ("One shared production dataset" in AGENTS.md).
* Oct 23, 2026 — local Postgres promoted to primary (`db/target.py`,
  `db/schema.py`, `scripts/lib/db_target.sh`, `scripts/mirror_db`); Neon demoted
  to an opt-in mirror reached with `--shared`, so a storage/egress cap can never
  stop the platform from running.
