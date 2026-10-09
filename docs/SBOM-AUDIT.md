# SBOM Audit — 2026-10-09

Companion to `docs/SBOM.json` (CycloneDX 1.6; root = `soc-plaform v1.1.0` @ `e9b26f4`, branch `dev-dalton`;
**42 components** = 39 PyPI + 3 frontend CDN libs).
Scope: the production Python environment (`.venv314`, Python 3.14.7) plus the three CDN libraries the web
shell loads. Method: `cyclonedx-py environment` snapshot from a throwaway tool venv → strict CycloneDX 1.6
schema validation → `pip-audit` from a separate throwaway 3.14 tool venv, run against both the exact
installed set (`pip freeze`) and the declared pins (`requirements.txt`) → cross-check of every component
against `requirements*.txt` and the repo's actual imports.

> **2026-10-09 cleanup**: the audit-tooling installs and strays documented as findings F1/F2 in the first
> edition of this audit have been **removed** from both venvs (`.venv314` 63→39 packages, legacy `.venv`
> 45→39) and this SBOM regenerated from the lean environment. The 24-component drop (66→42) is entirely
> F1/F2 packages plus their orphaned transitive deps; every remaining component traces to a declared pin.
>
> **Same-day follow-up — F3 resolved**: the frontend CDNs are now **version-pinned** in
> `web/index.modular.html` — `vue@3.5.43` (was floating `@3`) and `cdn.tailwindcss.com/3.4.17` (was
> unpinned; the live redirect target that day). Both pins were sha256-verified byte-identical to what
> served before, so behavior is unchanged — the page was re-verified live (full Vue mount, Tailwind
> styling, API 200s, no console errors).

## Verdict

- **No known vulnerabilities** — `pip-audit` exit 0, "No known vulnerabilities found", both against the
  exact installed freeze of `.venv314` and against `requirements.txt` (run from a throwaway 3.14 tool venv).
- **No declared-vs-installed drift**: all 15 canonical pins from `requirements.txt` + `requirements-dev.txt`
  are present in the venv at the pinned versions; `pip check` reports no broken requirements on either venv.
- **1 finding remains** (F4, accepted risk) — hygiene, not compromise. F1/F2/F3 are resolved; nothing in
  the venv or the loaded frontend is unexplained.

| # | Finding | Severity | Action |
|---|---|---|---|
| ~~F1~~ | ~~`pip-audit` + `cyclonedx-python-lib` (~20 transitives) installed in the production venv, declared nowhere~~ | ~~Low~~ **Resolved 2026-10-09** | Uninstalled from `.venv314`; audits/SBOM generation now run from throwaway tool venvs in `/tmp` |
| ~~F2~~ | ~~True strays: `redis`, `python-pptx` + `pillow`/`xlsxwriter`~~ | ~~Low~~ **Resolved 2026-10-09** | Uninstalled from both venvs (`.venv` also carried an orphaned `lxml` + `async-timeout`); SBOM regenerated |
| ~~F3~~ | ~~Frontend supply chain: `vue@3` floating major tag, Tailwind Play CDN unpinned~~ | ~~Medium~~ **Resolved 2026-10-09** | Pinned `vue@3.5.43` + `cdn.tailwindcss.com/3.4.17` in `web/index.modular.html` (sha256-verified identical to prior live bytes); axios already pinned at 1.6.0 |
| F4 | The Python **3.9 CI leg** intentionally keeps last-3.9 `starlette`/`pytest` with documented accepted risk (PYSEC-2026-248/2281/2280, -1845); the 3.14 production leg is clean | Accepted | Revisit when 3.9 leaves the CI matrix (tracker item #9) |

## A. Frontend runtime (3 components, loaded from CDNs in `web/index.modular.html`)

| Component | Version | What it is / why it's here |
|---|---|---|
| vue | 3.5.43 (**pinned**) | The entire UI shell — every tab component (`DatabaseTab`, `AnalysisTab`, …) is a Vue options component rendered by `createApp` in `app.modular.js`. Global build, no bundler. |
| axios | 1.6.0 (pinned) | HTTP client behind `web/modules/api.js`; every API call (canonical-first with legacy `/api/db/*` fallback) goes through it. |
| tailwindcss | 3.4.17 (**pinned**) | The Play CDN compiles Tailwind classes at runtime in the browser; all styling in the components is Tailwind utility classes. |

## B. Web framework + server (direct runtime pins and their chain)

| Component | Version | Role |
|---|---|---|
| fastapi | 0.141.1 | The API surface (`api/main.py`, `api/routes/*`) — routing, OpenAPI, dependency injection. |
| starlette | 1.7.0 | ASGI framework underneath FastAPI — middleware, CORS, TestClient base. Fixed branch of PYSEC-2026-248/2281/2280. |
| uvicorn | 0.54.0 | ASGI server the API runs on (`scripts/dev` / `scripts/start`, port 8000). |
| click | 8.5.0 | Transitive: uvicorn's CLI. |
| h11 | 0.16.0 | Transitive: zero-dependency HTTP/1.1 implementation used by uvicorn/httpcore. |
| python-multipart | 0.0.32 | Transitive (also direct pin): form/file uploads parsed by FastAPI/Starlette. |
| annotated-doc | 0.0.5 | Transitive: docstring annotation helper pulled by this FastAPI version. |

## C. Data layer

| Component | Version | Role |
|---|---|---|
| SQLAlchemy | 2.1.2 | ORM for all persistence (`db/models.py` — SplunkEvent, TriageResult, investigation state, etc.). |
| psycopg | 3.3.6 | PostgreSQL driver (Neon) — pinned as `psycopg[binary]`. |
| psycopg-binary | 3.3.6 | Transitive: the compiled libpq bundle that comes with the `[binary]` extra. |

## D. Validation, config, typing

| Component | Version | Role |
|---|---|---|
| pydantic | 2.13.5 | Request/response models (e.g. `PastedNotableRequest`) and settings validation. |
| pydantic_core | 2.46.5 | Transitive: Rust core of pydantic v2. |
| annotated-types | 0.8.0 | Transitive: shared constraint annotations for pydantic. |
| typing-inspection | 0.4.4 | Transitive: runtime introspection of type hints for pydantic. |
| typing_extensions | 4.16.0 | Transitive: backports of newer typing features across the stack. |
| python-dotenv | 1.2.3 | Loads `.env` bootstrap (BWS token + local-only keys) at API start. |

## E. Outbound HTTP clients

| Component | Version | Role |
|---|---|---|
| requests | 2.34.2 | Used by tools/intel features and Ollama/health checks. |
| urllib3 | 2.8.0 | Transitive: transport layer of requests. |
| certifi | 2026.7.22 | Transitive: CA bundle for TLS verification. |
| charset-normalizer | 3.5.1 | Transitive: response encoding detection for requests. |
| idna | 3.20 | Transitive: international domain names (requests/anyio/httpx). |
| httpx | 0.28.1 | Dev: backs starlette's `TestClient` on the 3.9 leg (see `requirements-dev.txt` header). |
| httpcore | 1.0.9 | Transitive: HTTP/1.1 transport for httpx. |
| httpx2 | 2.13.1 | Dev: starlette 1.6+ imports httpx2 natively; pinned explicitly so the 3.14 leg stops warning. |
| httpcore2 | 2.13.1 | Transitive: transport for httpx2. |
| truststore | 0.10.4 | Transitive: system-cert TLS integration required by httpcore2/httpx2 (kept after F1 cleanup — it is their dependency, not pip-audit's). |
| anyio | 4.15.1 | Transitive: async compatibility layer under starlette/httpx/psycopg-async. |

## F. Cryptography

| Component | Version | Role |
|---|---|---|
| cryptography | 50.0.1 | X.509 parsing for `Tools/pki_cert_decoder` (a CLI tool, not the API). |
| cffi | 2.1.1 | Transitive: Python↔C bindings for cryptography. |
| pycparser | 3.0 | Transitive: C header parser used by cffi. |

## G. Test stack (dev pins + transitives)

| Component | Version | Role |
|---|---|---|
| pytest | 9.1.1 | The 392-test suite (3.14 leg); 8.4.2 on the 3.9 leg (accepted risk, F4). |
| pluggy | 1.6.0 | Transitive: pytest's plugin system. |
| iniconfig | 2.3.0 | Transitive: ini-style config parsing for pytest. |
| packaging | 26.3 | Transitive: version/specifier parsing (pytest). |
| Pygments | 2.21.0 | Transitive: syntax highlighting in pytest tracebacks. |
| hypothesis | 6.168.4 | Property-based fuzz layer for the boundary ingest engines (`test_boundary_ingest_properties.py`). |
| sortedcontainers | 2.4.0 | Transitive: interval/ordering structures used internally by hypothesis. |
| pyflakes | 4.0.0 | The `scripts/check_undefined_names.sh` CI import lint (F821 gate). |

*(Removed in the F1 cleanup: `tomli`, which only pip-audit needed — pytest reads `pyproject.toml` via the
stdlib `tomllib` on 3.14.)*

## H. Environment tooling (1 component)

| Component | Version | Role |
|---|---|---|
| pip | 26.2.1 | The venv's own installer — part of the environment cyclonedx-py snapshots; not an application dependency. |

## Tool workflow — `scripts/sbom` (one command)

`scripts/sbom` runs the whole pipeline below: snapshot → post-process → schema-validate → double
pip-audit, from a pinned throwaway tool venv under `$TMPDIR` (nothing is ever installed in the project
venvs — finding F1 stays resolved). Flags: `--no-validate`, `--no-audit`. The underlying recipe:

```bash
# SBOM regeneration (jsonschema lives here too, for validation)
python3 -m venv /tmp/sbom-tool && /tmp/sbom-tool/bin/pip install cyclonedx-py jsonschema
/tmp/sbom-tool/bin/cyclonedx-py environment .venv314/bin/python --of json -o /tmp/sbom-raw.json \
  --output-reproducible --sv 1.6

# Vulnerability audit — use a 3.14 tool venv (a 3.9 one can't resolve 3.14-only pins)
python3.14 -m venv /tmp/audit-tool314 && /tmp/audit-tool314/bin/pip install pip-audit
.venv314/bin/python -m pip freeze --all > /tmp/venv314-freeze.txt
/tmp/audit-tool314/bin/pip-audit -r /tmp/venv314-freeze.txt    # exact installed set
/tmp/audit-tool314/bin/pip-audit -r requirements.txt            # declared pins
```

## Recommendations

1. ~~**Hash-pin**~~ — **Done 2026-10-09**: both requirements files are hash-locked (full closure of every
   pin, both marker branches, sdist + every platform wheel, linux-only deps included) by
   `scripts/pin_requirements.py`, which is idempotent and coverage-checked. pip auto-enables
   `--require-hashes` the moment any line carries a hash, so the closure must stay complete — regenerate
   with that script after any pin change. The marker gate validates hash shape, strips them, and keeps
   checking markers (verified: a bad marker with a valid hash still fails the gate).
2. Regenerate the SBOM in CI on every push to `dev-dalton` and diff it — dependency drift becomes a
   reviewable event instead of a surprise. `scripts/sbom` is the canonical entry point to wire into CI;
   regenerate on push and diff the result.
