# modules/ — domain logic & API

- `api.js` — every HTTP call (axios)
- `database.js`, `analysis.js`, `codeReview.js` — domain method maps + helpers (growing)

**Not the same as `components/`.**  
Components render UI. Modules hold behavior that is not pure and not visual.

Live `index.html` does not load these yet. `index.modular.html` does.
