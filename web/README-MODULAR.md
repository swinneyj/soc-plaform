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

## Files for testing (live site unchanged)

| File | Purpose |
|------|---------|
| `index.html` | **Live site** – still the monolith. Do not replace until you confirm modular works. |
| `index.modular.html` | **Test shell** – thin HTML that wires all components |
| `app.modular.js` | Root Vue app (logic from live + component registration) |
| `components/AnalysisTab.js` | **NEW** – extracted AI Analysis tab |
| `components/CodeReviewTab.js` | **NEW** – extracted Code Review tab |

## How to test

1. Serve the `web/` folder as you normally do (same backend).
2. Open **`/index.modular.html`** (or whatever path maps to it).
3. Click through every tab: Tools, Database, AI Analysis, Closure, Jobs, Reports, Code Review.
4. Live **`/index.html`** remains the original monolith.

When modular looks good, Piece 5 will swap live `index.html` to the modular shell (with a backup).

## Script load order in `index.modular.html`

1. Vue + axios (CDN)
2. `utils/*`
3. `modules/*`
4. `components/*` (all 8 tabs)
5. `app.modular.js`
