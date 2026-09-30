# modules/ — domain logic & API

- `api.js` — every HTTP call (axios)
- `database.js`, `analysis.js`, `codeReview.js` — domain method maps + helpers (growing)

**Not the same as `components/`.**  
Components render UI. Modules hold behavior that is not pure and not visual.

Both `index.html` (live) and `index.modular.html` now load these — cutover complete. Former `web/app.js` monolith and all `web/Old` copies are retired to `scripts/attic/` (`scripts/attic/app.js`, `scripts/attic/web-old/`).
