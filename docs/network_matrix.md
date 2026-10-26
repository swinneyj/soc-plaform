# Network Port & Protocol Matrix

Source of truth: `k8s/` manifests + `api/` + `scripts/`. For firewall and
`NetworkPolicy` approval before cluster onboarding.

## In-cluster flows

| # | Source | Destination | Port / Protocol | TLS | Policy control |
|---|---|---|---|---|---|
| 1 | Internet clients | `ingress-nginx` | 443/TCP (TLS) | Yes (Secret `soc-tls`) | `k8s/ingress-nginx.yaml` |
| 2 | `ingress-nginx` | `soc-frontend:80` | 80/TCP (cluster-internal) | No (terminated at Ingress) | Ingress rules `/` → frontend |
| 3 | `ingress-nginx` | `soc-api:8000` | 8000/TCP | No (terminated at Ingress) | Ingress rules `/api` → soc-api |
| 4 | `soc-api` pods | `soc-api` peers | 8000/TCP inbound ONLY from `ingress-nginx` ns | n/a | `k8s/netpol-api-ingress.yaml` (default-deny otherwise) |
| 5 | `soc-api` | `soc-postgres:5432` | 5432/TCP (PostgreSQL wire) | PG-native (no Ingress involved) | StatefulSet headless Service `soc-postgres` |
| 6 | `soc-api` | `soc-llm:11434` (vLLM/Ollama) | 11434/TCP (Ollama/OpenAI-compatible HTTP) | No (cluster-internal) | `k8s/netpol-llm-zero-egress.yaml` ingress: only from `app: soc-api` |
| 7 | `soc-llm` | `kube-dns` (`kube-system`) | 53/UDP + 53/TCP (DNS) | No | Zero-egress policy egress: DNS only — **no other egress** |
| 8 | Fluent Bit sidecar | external SIEM collector | 443/TCP (HTTPS webhook; env `SOC_LOG_WEBHOOK_HOST/PORT/URI`) | Yes (`tls On`, `tls.verify On`, Bearer `soc-log-forwarder`) | No in-cluster receiver; typical SIEM equivalents: HEC 8088/TCP, syslog-TLS 6514/TCP — set the webhook env to match |

## Local development (docker / daemon, not K8s)

| Source | Destination | Port | Notes |
|---|---|---|---|
| Browser / operator | API | 127.0.0.1:8000/TCP | `scripts/start`; loopback only (`API_BIND` default) |
| API | local Postgres | 127.0.0.1:5432/TCP | Homebrew `postgresql@16`, db `soc_platform` |
| API | Ollama | 127.0.0.1:11434/TCP | `scripts/start` launches Ollama when present |
| Tailscale peers | file share (SMB) | 445/TCP | Out of K8s scope; see `SETUP.md` |

## What is deliberately closed

- `soc-llm` has **no internet egress** (MED-03): model weights and prompts
  stay in-cluster. Any new egress (e.g. a model registry) needs an
  explicit `NetworkPolicy` egress rule + review.
- `soc-api:8000` accepts traffic **only** from the `ingress-nginx`
  namespace (K8S-01). Direct pod-to-pod 8000 (including from `soc-llm`)
  is denied.
- Client-supplied `X-Auth-Request-User` / `X-SSL-Client-S-DN` never
  survive the Ingress (`k8s/ingress-nginx.yaml:25` blanks them); only the
  auth proxy re-stamps them (HIGH-02/HIGH-03).
- The database has **no Ingress route and no NodePort** — reachable only
  from `soc-api` pods via the headless Service.
