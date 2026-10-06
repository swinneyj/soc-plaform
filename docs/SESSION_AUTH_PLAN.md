# Session Auth & Role Split Plan — analyst / admin

**Date:** 2026-09-30
**Status:** ✅ BUILT Sept 30, 2026 — C1B.1–C1B.4 landed behind `AUTH_MODE` (Phase C1
deliverable of `docs/EXECUTION_PLAYBOOK.md`; build commits `30eae76`, `4d4134e`,
`c795bdb`, + C1B.4). Flag-off behavior is byte-identical (full suite green with the
flag unset); the `AUTH_MODE=session` matrix is green; zero new dependencies — the
scrypt KDF falls back to stdlib PBKDF2-SHA256 on runtimes whose Python build lacks
OpenSSL scrypt (verify dispatches on the stored scheme prefix). This is a *historical
plan doc*; the auth seam it describes is the current seam.
**Goal:** same treatment as `docs/FRONTEND_MODULARIZATION.md` / `docs/BACKEND_MODULARIZATION_PLAN.md` —
extend the existing `api/auth.py` seam with per-user session auth and an analyst/admin
role split, additively, with the current API-key behavior byte-identical until a feature
flag flips on. This is the prerequisite for any non-localhost exposure (the
security-remediation tracker gates public exposure on exactly this work).

---

## 1. Problem & non-goals

**Problem.** The single armed `API_KEY` is the only credential today, and it cannot:

- distinguish **analysts** from **admins** — every holder can execute tools, delete jobs,
  and release Splunk-boundary batches;
- be **revoked per user** — rotation is a global outage for every client;
- stay **secret from its own users** — the UI needs the key, so the server injects it into
  `index.modular.html` (anyone who can load the dashboard holds the key).

**Non-goals (explicitly out of scope):** SSO/OAuth/SAML, password-reset email flows,
multi-tenancy/orgs, per-user audit logging (future), login rate limiting (owned by C2
"service hardening"), and any new third-party dependencies — stdlib only.

---

## 2. Design

- **Cookie sessions for the UI.** Cookie `soc_session`: `HttpOnly`, `SameSite=Lax`,
  `Secure` when `SESSION_SECURE=1` (default off for localhost; set it on Vercel/HTTPS).
  Value is `secrets.token_urlsafe(32)`; the database stores only its SHA-256 hash. Fixed
  12 h expiry (`expires_at`), no sliding renewal in v1. Logout deletes the row.
- **CSRF for cookie-authed mutations.** Every mutating request in session mode must carry
  `X-CSRF-Token` equal to the session's `csrf_token` (a second per-session random). The
  custom-header requirement defeats cross-site form posts (browsers cannot set headers on
  simple requests). The token is returned by `POST /api/auth/login` and
  `GET /api/auth/session`.
- **`X-API-Key` path stays for API clients.** A valid key authenticates as a machine
  actor with the **admin** role (it is a deployment credential used by automation).
  `?api_key=` stays accepted for smoke tests. 17 existing key-gate tests keep passing
  unchanged.
- **`api/auth.py` remains the single seam.** One new resolver —
  `resolve_actor(request) -> Actor(kind="session" | "api-key" | "anonymous", role=…,
  user=…, csrf_ok=…)` — and the existing `mutation_gate_rejects` / `require_api_key`
  are refactored on top of it. No other module learns how auth works.
- **Read gate (session mode only).** In `AUTH_MODE=session`, every `/api/*` read also
  requires an authenticated actor; excepted: `/health`, `/api/health`, and the
  `/api/auth/*` routes themselves. The current key gate deliberately leaves reads open —
  that changes only in session mode.
- **Feature flag.** `AUTH_MODE` env var: unset or `api-key` → today's behavior exactly;
  `session` → session + CSRF + role gates active (API key still accepted as admin).
  Rollback = unset the var and restart.

---

## 3. Roles

- **analyst** — the investigation flow: cases, notables, evidence, analyses, closure,
  rules/playbooks, reports, read-only Splunk search.
- **admin** — everything analyst has, plus machine/ops surfaces: tool catalog &
  execution & registry, job deletion/clearing, Splunk-boundary admit/release.
  Retention apply and backups remain **CLI-only** (shell access on the ops box = admin).

Role→family matrix (families per `docs/API_ENDPOINTS.md`):

| Route family | Read | Mutate |
|---|---|---|
| `/health`, `/api/health`, `/api/`, `/api/db/ollama/health` | open | — |
| `/api/db/stats`, `/api/db/operations` | analyst | — |
| `/api/db/triage*` — cases (list/detail/delete/batch-delete) | analyst | analyst |
| `/api/db/notables*` — paste, promote, delete, historical | analyst | analyst |
| `/api/db/triage/*/evidence*`, `/investigation-state`, `/notable` | analyst | analyst |
| `/api/analyses` (alias `/api/db/analyze`) | analyst | analyst |
| `/api/db/closure-note`, `/closure-readiness` | analyst | analyst |
| `/api/db/rules`, `/supportive-queries*`, `/placeholder-aliases*` | analyst | analyst |
| `/api/code-review*`, `/api/code-reviews*` | analyst | analyst |
| `/api/reports*`, `/api/tool-artifacts/*` | analyst | — |
| `/api/splunk/search-one` | analyst | — |
| `/api/splunk-boundary/status` | analyst | admin (`/admit`, batch release/delete) |
| `/api/tools*`, `/api/execute`, `/api/registry*`, `/api/tools/regression` | admin | admin |
| `/api/jobs*` | analyst | admin (delete one / clear all) |
| retention apply, backups | — | CLI only (`scripts/prune_analysis_results.py --apply`, `scripts/start --backup`) |

Roles only bind in `AUTH_MODE=session`. Today's armed-key behavior (key = full access,
reads open) is unchanged until then. (`/api/code-review/zip` accepts uploads and stays
analyst in v1 — flagged for the C2 hardening review.)

---

## 4. Storage

Additive tables in `db/models.py` (the lifespan `Base.metadata.create_all` picks them up —
migrations-free, same as every prior table):

```python
class User(Base):
    __tablename__ = "users"
    id            = Column(Integer, primary_key=True)
    username      = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)   # encoded, see below
    role          = Column(String(16), nullable=False, default="analyst")  # analyst|admin
    is_active     = Column(Boolean, nullable=False, default=True)
    created_at    = Column(DateTime, nullable=False, default=_utcnow)

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id            = Column(Integer, primary_key=True)
    token_hash    = Column(String(64), unique=True, nullable=False, index=True)  # sha256 hex of cookie value
    csrf_token    = Column(String(64), nullable=False)
    user_id       = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at    = Column(DateTime, nullable=False, default=_utcnow)
    expires_at    = Column(DateTime, nullable=False)
```
(`_utcnow` is `db/models.py`'s existing naive-UTC column-default callable — its convention
for every `created_at`; add `Boolean` and `ForeignKey` to its `from sqlalchemy import …`
line. `db/util.py:utcnow_naive` is the api-side clock used by `session_expiry` below.)

**Password hashing — stdlib `hashlib.scrypt` only** (no new deps): `n=2**14, r=8, p=1`,
16-byte random salt, `dklen=32`, encoded as `scrypt$n$r$p$salthex$hashhex`. Helpers
`hash_password(pw)` / `verify_password(pw, encoded)` live in `api/auth.py` (keeps the seam
single) and compare with `hmac.compare_digest`.

**User provisioning — CLI only.** `scripts/manage_users.py` with subcommands
`create <name> --role analyst|admin`, `list`, `set-role`, `set-password`, `deactivate`.
Passwords come from a `getpass` prompt or `--password-env VARNAME` (references an env var
by name so automation never puts a secret in argv or shell history).

---

## 5. Step plan (Pieces — additive-first, same as the modularization plans)

**Piece A — Models + user CLI (S).** `User` + `AuthSession` in `db/models.py`; scrypt
helpers in `api/auth.py`; `scripts/manage_users.py`. No behavior change anywhere (flag is
off; tables inert). Verify: create a user on the dev DB via the CLI, `list` shows it.

**Piece B — Routes + middleware (M).** New `api/routes/auth.py`:
`POST /api/auth/login` (JSON `{username, password}` → sets `soc_session`, returns
`{user, role, csrf_token}`), `POST /api/auth/logout`, `GET /api/auth/session`
(→ `{user, role, csrf_token}` or 401). `resolve_actor()` in `api/auth.py`; the mutation
gate in `api/main.py` extends to: in session mode require `(session ∧ CSRF)` **or** valid
API key; add `require_role("admin")` as a FastAPI dependency on the admin families from §3;
add the session-mode read gate. **Doc-drift guard note:** register the router in
`api/main.py` and add literal rows for the three new routes to `docs/API_ENDPOINTS.md`
(the `TestCanonicalRestPaths` guard fails otherwise) — no legacy `/api/db/*` spelling, so
`tests/api_path_contract.json` is untouched.

**Piece C — UI (M–L).** Login modal + user chip + logout in `HeaderNav.js`;
`web/utils/auth.js` stamps `X-CSRF-Token` from `window.__SOC_CSRF__` (seeded from
`/api/auth/session`, refreshed after login); `app.modular.js` maps a 401 on mutation to
the login modal; role-aware buttons — for analysts, hide-with-tooltip the admin surfaces
(Tabs: Tools execute/regression/registry-reload buttons; JobsTab delete/clear buttons;
SplunkBoundaryWidget admit/release buttons), same pattern as the disabled
historical-promote tooltip. Bump the `?v=` cache-buster of **every** touched web file in
`web/index.modular.html`.

**Piece D — Tests + docs (M).** Auth matrix in `tests/test_api_key_gate.py` (the existing
auth test home — never create new test files): login ok / bad password → 401; logout
invalidates the cookie (subsequent session GET → 401); CSRF missing or wrong on a
mutation → 403; analyst hits admin route → 403; API key still passes in session mode;
expired session → 401; flag-off behavior byte-identical (existing 17 auth tests + 276
suite stay green). Then: `docs/API_ENDPOINTS.md` rows (if not done in Piece B), a dev-plan
note, and this doc's `Status` line updated.

---

## 6. Risk controls & rollback

- **Additive first.** Piece A lands inert; nothing changes behavior until
  `AUTH_MODE=session` is set. The armed API-key gate is **not** removed at any point.
- **Rollback:** unset `AUTH_MODE`, restart (`scripts/start --stop` then `scripts/start`).
  The new tables are inert and harmless.
- **Never-log rule:** logs and error bodies may contain usernames and actor kinds only —
  never passwords, tokens, cookie values, or CSRF tokens.
- **Flag-off test** is part of Piece D: the whole existing suite must pass with the flag
  unset, proving byte-compat.
- **Seam discipline:** only `api/auth.py` and the one gate in `api/main.py` know auth
  exists; route files take `Depends(require_role(...))` and nothing more.

---

## 7. Size target & exit criteria

| Piece | Files | Est. lines | Effort |
|---|---|---|---|
| A — models + CLI | `db/models.py`, `api/auth.py`, `scripts/manage_users.py` | ~180 | S |
| B — routes + middleware | `api/routes/auth.py`, `api/auth.py`, `api/main.py` | ~250 | M |
| C — UI | `HeaderNav.js`, `app.modular.js`, `web/utils/auth.js`, touched tabs | ~350 | M–L |
| D — tests + docs | `tests/test_api_key_gate.py`, docs | ~300 | M |

**Exit criteria (from the playbook):** tests prove login, logout, CSRF reject, role gate,
and API-key-still-works; zero new dependencies; the three `/api/auth/*` routes appear
literally in `docs/API_ENDPOINTS.md`; full suite green on 3.14 **and** 3.9 with the flag
off; `AUTH_MODE=session` matrix green; all UI harness scenarios still green (23 at C1B
time — the count grows to 24 when the playbook's C3.3 lands).

**Fast checks (no runtime needed):** deterministic scrypt verify unit test (fixed salt
vector); `bash scripts/check_undefined_names.sh`; `node --check` on touched web files.

---

## Related

- The seam this extends: `api/auth.py` (`_API_KEY`, `require_api_key`,
  `mutation_gate_rejects`, `request_has_api_key`) and the gate in `api/main.py`.
- Frontend stamping precedent: `web/utils/auth.js` (`X-API-Key` interceptor).
- Predecessors: `docs/FRONTEND_MODULARIZATION.md`, `docs/BACKEND_MODULARIZATION_PLAN.md`
  (same additive-then-cutover shape).
- Executor runbook: `docs/EXECUTION_PLAYBOOK.md` (this doc = C1; the build = follow-up
  stages).
