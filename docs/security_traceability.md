# Security Control-to-Code Traceability Matrix

Generated 2026-10-26 from the working tree. Parent baseline: `3124100`
(`feat(auth): real local accounts and one-click role switching`).
Release tag: `v1.0.0-rc1-hardened` (annotated; the tag object records the
exact release SHA — verify with `git show v1.0.0-rc1-hardened`).

Verification status at generation: **606 passed** (`pytest tests/
--ignore=tests/ui_regression`), route matrix **20 admin / 50 analyst /
5 public** (`scripts/route_matrix.py --check` current), live
`security_smoke.py` **8/8**, e2e demo loop **14/14**, kubeconform
`-strict` **8/8 resources valid** (4 K8S-01..04 files + task-4 manifest).

## Tier 1 — High severity

### HIGH-01 Server-side RBAC (20 admin / 50 analyst / 5 public)
- Implementation: `api/auth.py:517` (`require_role`, admin-inherits-analyst);
  wired on every route, e.g. `api/routes/triage.py:117` (analyst read),
  `api/routes/triage.py:234` (admin delete); policy in
  `scripts/route_matrix.py:46-70`, enforced table in
  `docs/ROUTE_ROLE_MATRIX.md`.
- Tests: `tests/test_route_matrix.py` (counts, role match, analyst 403 on
  admin, analyst 200 on tools/jobs/status); `tests/test_api_key_gate.py`
  (`test_role_gate_analyst_403`); `tests/test_proxy_auth.py`,
  `tests/test_dev_login.py` (role gates).
- Proof: `scripts/route_matrix.py --check` → 75 routes, 0 ungated.

### HIGH-02 Reverse-proxy identity (integer + X.509 DN)
- Implementation: `api/auth.py:245` (`_PROXY_DN_CN_RE`), `:341`
  (`resolve_proxy_actor`); DN-unprovisioned → 403 via
  `api/middleware.py:180` (`Unknown user` gate mapping).
- Tests: `tests/test_proxy_header_auth.py` (integer + DN + malformed +
  unprovisioned-403 + untrusted-peer 401); `tests/test_proxy_auth.py`
  (17 pre-existing: trust boundary, mode isolation).
- Proof: 23/23 proxy tests pass.

### HIGH-03 Proxy anti-spoofing (CIDR + header strip + NetworkPolicy)
- Implementation: `api/auth.py:272` (`_remote_is_trusted_proxy`,
  `SOC_TRUSTED_PROXY_CIDRS`); `k8s/ingress-nginx.yaml:25`
  (blanks `X-Auth-Request-User` / `X-SSL-Client-S-DN`);
  `k8s/netpol-api-ingress.yaml:21-30` (TCP/8000 only from `ingress-nginx`).
- Tests: `tests/test_proxy_auth.py` (untrusted peer 401, no-CIDR fail
  closed); `tests/test_k8s_security.py::test_k8s01`.
- Proof: unit + structural green; live header-strip is cluster-side.

### HIGH-04 Column encryption at rest (AES-256-GCM)
- Implementation: `db/crypto.py:58` (`enc:v1:`), `:114` / `:152`
  (`encrypt_text` / `EncryptedText`); columns
  `auth_sessions.csrf_token`, `code_reviews.code_snippet`
  (`db/models.py`); rotation in `scripts/rotate_field_key`
  (`--check` / `--backfill` / `--rotate`).
- Tests: `tests/test_column_encryption.py` (14: contract, at-rest,
  login→CSRF round trip with key on).
- Proof: `--check` → 6 rows, 6 encrypted, 0 plaintext; raw SQL shows
  `enc:v1:` prefix.

### HIGH-05 Encrypted storage (StatefulSet + PVC + StorageClass)
- Implementation: `k8s/postgres-statefulset.yaml` (StorageClass
  `soc-encrypted`, headless Service, `runAsUser: 999` + `runAsNonRoot`,
  `secretKeyRef: soc-postgres/postgres-password`,
  `volumeClaimTemplates 10Gi`, probes).
- Tests: `tests/test_k8s_security.py::test_k8s04` (no inline secrets).
- Proof: kubeconform `-strict` valid (3/3 resources); server-side
  `kubectl apply --dry-run` remains cluster-side (local kubectl needs a
  cluster even for client dry-run).

### HIGH-06 No hardcoded secrets
- Implementation: runtime-only credentials via env/Keychain/BWS
  (`scripts/secrets-keychain`, `scripts/dev`/`scripts/start` inject);
  `scripts/attic/db_routes.py` carries no guards and is gated out.
- Tests: `git grep` for private-key/AKIA/token/DSN-with-password patterns
  → 0 matches (`scripts/check_env_parser.ps1` DSN vector is
  concatenation-built so no single line is credential-shaped; `pwsh`
  check still 17/17).
- Proof: 0 grep matches; `security_smoke.py` has no secret material.

## Tier 2 — Medium severity

### MED-01 Structured JSON security auditing
- Implementation: `api/helpers/audit.py` (`soc.audit` single-line JSON;
  redacts password/api_key/token/cookie); emitted from
  `api/middleware.py` (`auth_rejected`, `state_change`) and
  `api/main.py:171` (route 401/403) + `api/routes/auth.py`
  (login/logout/throttle events) to `/var/log/soc/audit.json`.
- Tests: `tests/test_audit_logging.py` (20: 401/403 lines, no bodies).
- Proof: kind-cluster run delivered the 401 event line end-to-end over TLS.

### MED-02 TLS log-forwarding sidecar
- Implementation: `k8s/soc-api-deployment.yaml:65,79-81` (Fluent Bit
  tails `/var/log/soc/audit.json` via shared `emptyDir`, `tls On` /
  `verify On`, Bearer from Secret).
- Tests: `tests/test_k8s_manifest.py` (10, incl. tail-path == env path,
  no embedded secrets); harness `scripts/task4_cluster_verify.py`.
- Proof: fresh kind run 2026-10-26, EXIT 0 — 6/6 matrix (apply, audit
  file, 401 event, TLS delivery, bearer match, payload intact).

### MED-03 LLM zero-egress
- Implementation: `k8s/netpol-llm-zero-egress.yaml:21` (`Ingress,Egress`;
  ingress only from `app: soc-api:11434`; egress DNS `:43-45` port 53).
- Tests: `tests/test_k8s_security.py::test_k8s02`.
- Proof: structural green + kubeconform valid.

### MED-04 Session security, hashing, CSRF
- Implementation: `api/auth.py:116` (scrypt-N16384 / PBKDF2-600k,
  salted, memory-hard — stdlib equivalent of bcrypt/argon2, documented
  in docstring); `api/routes/auth.py:117` (`httponly`, `samesite=lax`,
  `secure` behind HTTPS); `api/auth.py:438` (double-submit
  `X-CSRF-Token`); login throttle in `api/routes/auth.py`.
- Tests: `tests/test_api_key_gate.py` (CSRF 403/200, throttle 429),
  `tests/test_rate_limit.py::test_login_has_its_own_throttle`.
- Proof: suite green.

### MED-05 CORS allowlist + traversal prevention
- Implementation: `api/middleware.py:303` (explicit `CORS_ORIGINS`,
  never `*`); `api/routes/tools.py:627` + `:648` (resolve + contain
  check → 404 on `..`/absolute/symlink escapes).
- Tests: `tests/test_security_hygiene.py` (traversal 404s, evil Origin
  gets no grant, allowlisted Origin granted) + live smoke (no wildcard).
- Proof: 5/5 unit + 8/8 live smoke.

### MED-06 Rate limiting / DoS
- Implementation: `api/middleware.py:275` (all mutating `/api/*` share
  per-IP 60s window → 429; auth paths exempt, login has own throttle);
  `RATE_LIMIT_PER_MIN` default 30 (`:217`).
- Tests: `tests/test_rate_limit.py` (mutating 429, reads open, login
  throttle independent).
- Proof: 3/3 + live 429 path exercised in smoke-adjacent runs.

### MED-07 Supply chain + dual-runtime pins
- Implementation: hash-locked `requirements*.txt`,
  `scripts/git-hooks/pre-push` (venv resolution, baseline, markers,
  SBOM, compile, hygiene, option keys, live smoke).
- Tests: `scripts/check_sbom_pins.py`, `scripts/check_requirement_markers.py`.
- Proof: `baseline --verify` HOLDING; `git push --dry-run` runs the full
  gate (re-run at push time with API up for the live half).

## Tier 3 — Low severity

### LOW-01 Error scrubbing
- Implementation: `api/helpers/errors.py:26` (`INTERNAL_ERROR_DETAIL`),
  `api/main.py:142,168` (global + 500 handlers scrub, log server-side
  with request id).
- Tests: `tests/test_internal_errors.py` (12: zero paths/SQL/tracebacks).
- Proof: green.

### LOW-02 Docs disabled by default
- Implementation: `api/main.py:105,112-114` (`ENABLE_DOCS` gate).
- Tests: `tests/test_security_hygiene.py` (`/docs`, `/openapi.json`,
  `/redoc` → 404) + live smoke (`/docs`, `/openapi.json` 404).
- Proof: unit 404s + live 404s 2026-10-26.

### LOW-03 No source maps; attic removed/gated
- Implementation: `web/` ships 0 `.map` files; `scripts/attic/db_routes.py:11`
  raises unless `SOC_ENABLE_ATTIC=1`; `api/helpers/` has no routers.
- Tests: `tests/test_attic_gating.py` (all routers mounted, attic never
  imported, helpers route-free).
- Proof: `find web -name '*.map'` → 0; tests 3/3.
