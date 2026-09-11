# modules/ — domain logic & API

- `api.js` — every HTTP call (axios)
- `database.js`, `analysis.js`, `codeReview.js` — domain method maps + helpers (growing)

**Not the same as `components/`.**  
Components render UI. Modules hold behavior that is not pure and not visual.

Both `index.html` (live) and `index.modular.html` now load these — cutover complete. Former `web/app.js` monolith archived to `Old/app.js-pre-modular-monolith.bak`.
