# Modular front-end layout (Piece 3 + 4)

## Why three folders? (not confusing)

| Folder | Role | What belongs here |
|--------|------|-------------------|
| **`components/`** | **UI only** (Vue components) | Templates, local UI state, props in / events out. No axios. |
| **`modules/`** | **Domain logic + API** | HTTP (`api.js`), later: database/analysis/closure method groups. |
| **`utils/`** | **Pure helpers** | Key generators, query renderers, selection math. No `this`, no network. |

Think of it as:
- **components** = screens / tabs you *see*
- **modules** = work the app *does*
- **utils** = small pure functions used by both

This is a standard split and makes troubleshooting clearer:
- Broken layout / button → `components/XxxTab.js`
- Wrong API payload / 500 → `modules/api.js`
- Bad placeholder substitution → `utils/queryRender.js`

## Current state (cutover complete — Piece 5 done)

| File | Purpose |
|------|---------|
| `index.html` | **Live site** – now the thin modular shell (was monolith pre-Piece 5). Backup at `Old/index.bak` / `index.html.bak`. |
| `index.modular.html` | **Reference test shell** – kept for side-by-side comparison / QA |
| `app.modular.js` | Root Vue app (data, computed, spreads, mounted) |
| `components/*` | All 8 tab components |
| `modules/*` | Domain modules (database, analysis, closure, codeReview, tools, api) |

> Stale `web/app.js` (2079-line monolith) has been archived to `Old/app.js-pre-modular-monolith.bak`. Do not reintroduce it — use `app.modular.js`.

## How to test / verify

1. Serve the `web/` folder (same backend, or static file server for UI-only check).
2. Open **`/`** (→ `index.html` live shell) and verify all tabs: Tools, Database, AI Analysis, Closure, Jobs, Reports, Code Review.
3. Optionally open **`/index.modular.html`** for side-by-side comparison — should be identical to `/`.
4. Hard-refresh after script changes (Ctrl+Shift+R) — browsers cache old `index.html` aggressively.

## Script load order (both `index.html` and `index.modular.html`)

1. Vue + axios (CDN)
2. `utils/*`
3. `modules/*` (api.js first, then domain modules)
4. `components/*` (all 8 tabs + HeaderNav)
5. `app.modular.js`
