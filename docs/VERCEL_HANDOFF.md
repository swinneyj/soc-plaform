# Vercel Handoff Package (D1)

*Assembled Sept 30, 2026 (execution-playbook stage D1). Audience: repo owner /
Vercel admin. Goal: make the unblock a conversation, not a project.*

## 1. Env vars to set in Vercel

| Var | Value | Notes |
|---|---|---|
| `DATABASE_URL` | Neon connection string, **rotated value** | Copy from the BWS console (org SSC-Lewis → project SOC Platform) — connection strings come from the console copy button only, never from prior messages/chats. Rotation history: `docs/security-remediation-tracker.md` §1. |
| `API_KEY` | The armed key | Same value BWS serves locally. Arms the mutation gate: every mutating `/api/*` request requires it. |
| `CORS_ORIGINS` | Comma-separated preview/production origins | e.g. `https://soc-plaform-livid.vercel.app` plus any preview domains the UI is served from. |
| `ENABLE_DOCS` | *(leave unset)* | Docs routes (`/docs`) stay disabled in production — deliberate hardening. Setting it to `1` would expose the OpenAPI UI publicly. |

## 2. Open question — routing (capture the answer during handoff)

There is **no `vercel.json` in the repo**; routing lives in the Vercel dashboard
config. The question that decides whether browser auth works on Vercel:

> Does a request for `/index.modular.html` hit the FastAPI function, or is the
> document served as a static asset from Vercel's CDN?

- **Through the function** → the server-side `window.SOC_CONFIG` injection
  (armed-key bootstrap, landed Sept 30, commit `99ee625`) applies, and the UI
  authenticates out of the box.
- **From the CDN** → the injection is skipped, `window.SOC_CONFIG.apiKey` is
  undefined, and **every UI write fails 401** once `API_KEY` is armed.

Fix options if it's the CDN:

1. **(a) Route the document through the function** — a dashboard rewrite/route
   so `/index.modular.html` is served by the API (preferred: no build change).
2. **(b) Build-time injection in `deploy.yml`** — a step that stamps
   `window.SOC_CONFIG` into the served HTML from a Vercel secret before
   `vercel build` (works regardless of routing, but bakes the key into the
   artifact).

Note either way: anyone who can load the page can read the injected key —
inherent to browser-side auth, acceptable while the UI is localhost/Tailscale
or access-restricted. Session auth + roles (playbook C1B) is the real fix
before any wider exposure.

## 3. Deploy pipeline (current, as-built)

`.github/workflows/deploy.yml` fires on push to `main`, `dev`, `dev-dalton`,
`dev-justin` — **every one of these deploys `--prod` to the same domain; last
push wins.** Steps:

1. **Baseline repo-state guard** — checks platform artifacts exist
   (`scripts/dev`, `scripts/pull-secrets`, `scripts/baseline`, `SETUP.md`,
   `AGENTS.md`, valid `.baseline.json`, ≥1 test file).
2. `vercel pull` → `vercel build --prod` (prebuilt) → `vercel deploy --prod`.
3. **Smoke test** — resolves a stable public domain, then polls
   `GET /api/health` (10 attempts × 10 s). Expected body:
   `{"status": "healthy", "timestamp": "…"}` with HTTP 200.

A red smoke test after a push usually means env vars (see §4) — check them
first.

## 4. What breaks without each var

| Missing/wrong var | Symptom |
|---|---|
| `DATABASE_URL` | Health may still return 200 (degraded, health-only); every data route fails. Deploy smoke can pass while the app is unusable. |
| `DATABASE_URL` stale (pre-rotation) | Auth errors against Neon; log-spam `FATAL` auth failures. Always re-copy from the BWS console. |
| `API_KEY` | Mutations (`POST/PUT/PATCH/DELETE` on `/api/*`, and any delete-shaped path) are **open to the public internet** — this is the dangerous one. |
| `CORS_ORIGINS` wrong/missing | Browser UI blocked by CORS on cross-origin calls (writes fail visibly in the console). |
| `ENABLE_DOCS` set to `1` | OpenAPI docs exposed publicly — leave unset. |

## 5. Handoff checklist (owner)

- [ ] `DATABASE_URL` set in Vercel (current rotated value, copied from BWS console).
- [ ] `API_KEY` set in Vercel (same value BWS serves locally).
- [ ] `CORS_ORIGINS` set (preview + production origins).
- [ ] §2 routing question answered; document route/function decision here:
      *(answer + date)* ____________ (apply option (a) or (b) if CDN).
- [ ] Push a no-op commit to a deploy branch and confirm: smoke green **and**
      one UI write succeeds against prod (proves injection path end-to-end).

## 6. User-side hygiene (owner: human — not automatable, D3)

- [ ] **Safari history scrub** — past sessions may hold URLs/queries that
      shouldn't linger on a shared machine.
- [ ] **Thread-history deletion decision** — agent chat threads persist
      server-side (retained until a deletion request completes). Decide
      whether/what to request deletion for; requests go through support
      (CCPA 45-day / GDPR 1-month clocks). See
      `docs/security-remediation-tracker.md`.
