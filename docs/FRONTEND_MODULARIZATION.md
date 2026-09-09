# Frontend Modularization

**Date:** 2026-09-04  
**Scope:** `web/` UI only (Vue 3 SPA). Backend / API unchanged.  
**Goal:** Break the monolithic `index.html` into small, domain-owned files so troubleshooting and future changes stay local.

---

## Before vs After

| | Before | After |
|---|--------|--------|
| Live entry | One file ~280 KB / ~4,800 lines (HTML + all Vue logic) | Thin shell ~15 KB (`index.html`) |
| Tab UI | Inline `v-if` blocks in the monolith | 8 Vue components under `web/components/` |
| Business logic | ~91+ methods in one root options object | Domain modules under `web/modules/` |
| HTTP | Scattered `axios` calls | Central `web/modules/api.js` |
| Pure helpers | Mixed into Vue methods | `web/utils/` (no `this`, no network) |
| Root app | Everything | `app.modular.js` — `data`, `computed`, method spreads, `mounted` (~430 lines) |

---

## Folder map

```
web/
├── index.html                 ← LIVE thin shell (script tags + component tags only)
├── app.modular.js             ← root Vue createApp: data, computed, spreads, lifecycle
├── components/                ← UI only (templates, props, emits)
│   ├── HeaderNav.js
│   ├── ToolsTab.js
│   ├── DatabaseTab.js
│   ├── AnalysisTab.js
│   ├── ClosureTab.js
│   ├── JobsTab.js
│   ├── ReportsTab.js
│   └── CodeReviewTab.js
├── modules/                   ← domain logic + API
│   ├── api.js                 ← every HTTP endpoint
│   ├── database.js            ← Database tab methods
│   ├── analysis.js            ← AI Analysis / phase2 / supportive / aliases
│   ├── codeReview.js          ← Code Review methods
│   ├── closure.js             ← Closure note methods
│   └── tools.js               ← Tools, Jobs, Reports, health
└── utils/                     ← pure helpers (optional use from modules)
    ├── keys.js
    ├── queryRender.js
    ├── selection.js
    ├── pocSummary.js
    └── codeSections.js
```

### What belongs where

| Folder | Responsibility | When something breaks, open… |
|--------|----------------|------------------------------|
| **`components/`** | What the operator **sees** — markup, local UI state, emit events upward | Layout, buttons, missing props, tab-specific display |
| **`modules/`** | What the app **does** — API calls, domain workflows, mutations of root state via `this` | Wrong data, failed actions, business rules |
| **`modules/api.js`** | HTTP only | Network errors, wrong paths, response shape |
| **`utils/`** | Pure functions (no Vue `this`, no axios) | Key generation, placeholder render, selection math |
| **`app.modular.js`** | Root `data()`, `computed`, wiring spreads, `mounted` / intervals | Missing shared state, poll timers, initial load |

Components must not call `axios` directly. They receive data via **props** and request actions via **emits**; the root (or domain modules mixed into the root) owns the work.

---

## Load order (live `index.html`)

Scripts must load in this order so globals exist before the app boots:

1. Vue + axios (CDN)
2. `utils/*` (optional pure helpers)
3. `modules/api.js`
4. `modules/database.js`, `analysis.js`, `codeReview.js`, `tools.js`, `closure.js`
5. `components/*` (all tab components + HeaderNav)
6. `app.modular.js` (registers components, `createApp`, `mount`)

Domain modules attach to `window` (e.g. `window.DatabaseMethods`). The root spreads them:

```js
methods: {
  ...(window.DatabaseMethods || {}),
  ...(window.AnalysisMethods || {}),
  ...(window.ToolsMethods || {}),
  ...(window.ClosureMethods || {}),
  ...(window.CodeReviewMethods || {}),
}
```

---

## Work completed (phased, site kept running)

All steps were **additive** until the final cutover. Live monolith stayed until Piece 5.

| Piece | What | Risk to live site |
|-------|------|-------------------|
| **1** | `modules/`, `utils/`, `modules/api.js`, backup of live index | None (additive) |
| **2** | Pure utils + domain module stubs / helpers | None |
| **3–4** | `AnalysisTab.js`, `CodeReviewTab.js`, `index.modular.html` + `app.modular.js` for side-by-side test | None (test URL only) |
| **Code Review fix** | Props/emits for find + drag handlers so switching tabs after Code Review stayed healthy | Test shell only |
| **5** | Live `index.html` ← thin modular shell; backup `index.live-backup-before-piece5.html` | Controlled cutover |
| **6** | Peel **Database** methods → `modules/database.js` | Logic move only |
| **6b** | Peel **Analysis** methods → `modules/analysis.js` | Logic move only |
| **6c** | Peel **Code Review**, **Tools/Jobs/Reports**, **Closure** → matching modules | Logic move only |

### Approximate sizes (post-modularization)

| File | Lines (approx.) |
|------|-----------------|
| `app.modular.js` | ~430 |
| `modules/analysis.js` | ~1,090 |
| `modules/codeReview.js` | ~1,050 |
| `modules/database.js` | ~380 |
| `modules/api.js` | ~190 |
| `modules/tools.js` | ~80 |
| `modules/closure.js` | ~150 |
| Components (each) | ~40–610 |

---

## Troubleshooting quick map

| Symptom | First place to look |
|---------|---------------------|
| Tab layout / controls wrong | `components/<Tab>.js` |
| API 4xx/5xx or wrong payload | `modules/api.js` then calling module |
| Triage / notables / promote / delete | `modules/database.js` |
| Run analysis, phase2, supportive queries, aliases | `modules/analysis.js` |
| Code review find, sections, upload, submit | `modules/codeReview.js` |
| Closure note generate / download | `modules/closure.js` |
| Tools execute, jobs list, reports, health badge | `modules/tools.js` |
| Shared state missing after load | `app.modular.js` (`data`, `mounted`) |
| Placeholder / key / selection math | `utils/` |

Browser console errors that name a missing method usually mean a method was not exported on the domain object or the spread in `app.modular.js` was omitted.

---

## Backups on disk (rollback)

Under `web/`:

| File | Purpose |
|------|---------|
| `index.live-backup-before-piece5.html` | Full pre-modular **monolith** (restore live HTML if needed) |
| `index.live-backup-20260904.html` | Earlier monolith snapshot from Piece 1 |
| `app.modular.js.bak-before-db-peel` | Root app before Database peel |
| `app.modular.js.bak-before-analysis-peel` | Before Analysis peel |
| `app.modular.js.bak-before-cr-tools-peel` | Before Code Review / Tools / Closure peel |
| `modules/*.bak-before-full` | Module files before full method bodies were written |

**Restore live UI to old monolith:**

```bash
cp web/index.live-backup-before-piece5.html web/index.html
```

Then hard-refresh the browser (Ctrl+Shift+R).

**Restore only root app logic** (example — Database peel):

```bash
cp web/app.modular.js.bak-before-db-peel web/app.modular.js
cp web/modules/database.js.bak-before-full web/modules/database.js
```

---

## Design rules (keep these)

1. **One domain per module** — do not put Analysis HTTP + Closure UI logic in the same file.
2. **Components stay presentational** — props in, emits out; no direct `axios`.
3. **API layer is the only place that knows paths** — modules call `API.*`, not raw URLs.
4. **Prefer pure utils** for anything that does not need `this` (easier to test in isolation).
5. **Additive changes first** — new files and spreads before deleting working code from the root.
6. **Hard-refresh after script list changes** — browsers cache old `index.html` and JS aggressively.

---

## Optional follow-ups (not required)

- Move more pure logic from domain modules into `utils/` and unit-test those helpers.
- Replace global `window.*Methods` with ES modules (`type="module"`) or a small bundler (Vite) when the team wants imports instead of script-tag order.
- Slim `data()` in `app.modular.js` by grouping related state per domain (still one root instance unless you adopt Pinia/Vuex later).
- Remove or archive `app.js` / `index.modular.html` / old `.bak` files once the team is confident in the modular live path.

---

## Related docs

- `web/README-MODULAR.md` — short folder legend next to the code
- `docs/REPO_MAP.md` — broader repository layout
- `docs/SYSTEM_VISUAL_MAP.md` — system-level diagrams
- `docs/NEW_DEVELOPER_ONBOARDING.md` — onboarding entry points

---

## Summary

The frontend was modularized **without taking the site down**: API centralized, all tabs extracted to components, and **all major business methods** moved into domain modules. The live entry is a thin HTML shell; operators still use the same URL. Failures now map to a single folder or file instead of a multi-thousand-line monolith.
