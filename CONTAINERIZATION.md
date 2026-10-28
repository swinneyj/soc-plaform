# SOC Platform Dockerized

Complete containerization and REST API wrapper for the SOC Orchestration Platform. Run the toolkit cross-platform with Docker or access it programmatically via FastAPI REST endpoints.

## Quick Start

### Option 1: FastAPI REST API (Recommended)

```bash
docker compose --profile api up -d
```

API is now available at `http://localhost:8000`

- **Swagger UI**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/health
- **Tools Catalog**: http://localhost:8000/tools

## Architecture

### Dockerfile (Multi-Stage Build)

- **Builder stage**: Installs dependencies (fastapi, uvicorn, pydantic, requests, python-dotenv)
- **Runtime stage**: Minimal Python 3.14 slim image
- **Volumes**: Data, Playbooks, and Tools are mounted separately
- **Healthcheck**: Container-native health checks for orchestration platforms

### docker-compose.yml

Three services:

1. **api-service** (default): FastAPI on `0.0.0.0:8000`
   - Auto-reload on code changes
   - Volume-mounted data directories

2. **postgres**: Default runtime database backend
  - Image: `postgres:16-alpine` (compose default).
  - Exposed on configurable host port (default `5433`).
  - Intended for repo-managed local runtime use.
  - **Version skew warning:** this compose image is PostgreSQL 16, but the live
    system-of-record database (Neon) runs PostgreSQL 18. That matters when you
    restore a prod dump locally: `pg_dump` must be at least the server's major
    version, so a 16-era client will refuse a 18 dump. The backup doc
    (`docs/BACKUP_AND_RESTORE.md`) covers the `pg_dump` version requirement and
    the `libpq`/matching-`postgresql@N` fix. If you restore a Neon dump into the
    compose 16 container, expect a version-mismatch failure, not a silent success.

### Cross-Platform Runtime Scripts

In addition to the Docker Compose path, the repo ships native startup/backup scripts
written once in PowerShell Core (`#!/usr/bin/env pwsh`) so they run on the same
code base on Windows, macOS, and Linux without separate per-platform copies.

The bash originals are still present for macOS-only environments; the pwsh versions
are the cross-platform equivalents.

| Task | Bash (macOS-native) | PowerShell Core (cross-platform) |
|------|---------------------|----------------------------------|
| Start the API after Postgres is up | `scripts/start_soc_api.sh` | `scripts/start_soc_api.ps1` |
| Daily pg_dump backup + gzip + retention | `scripts/backup_db.sh` | `scripts/backup_db.ps1` |

Key differences from the bash originals:

- **No hardcoded Homebrew paths.** `start_soc_api.ps1` probes `ollama` on `PATH`
  and then checks common install locations across Windows, Homebrew-on-Intel,
  Homebrew-on-Apple-Silicon, and the macOS app bundle. `backup_db.ps1` finds
  `gzip` the same way and falls back to system `/usr/bin/gzip` on Linux.
- **Port probing uses .NET `TcpClient`** instead of `nc -z`, so it works wherever
  PowerShell Core runs.
- **`.env` loading** is shared logic between the two scripts (same repo-root-first
  fallback as the bash originals).
- **Uvicorn runs as a foreground child process** rather than `exec` — functionally
  equivalent for interactive use; only the process-tree shape under a Windows
  service manager differs from the bash `exec` version.
- **Backup delegates to `scripts/export_postgres_dump.ps1`**, which already handles
  driver-suffix stripping and the Docker fallback, then adds gzip + retention on top.

Limitations (same as the bash originals, carried forward):

- PostgreSQL must be listening on `127.0.0.1:5432` (the scripts do not start it).
- A working venv Python must be present for uvicorn.
- `backup_db.ps1` needs `pg_dump` (or Docker + a running `postgres` service) and
  `gzip` on PATH.
- Temp files for the backup pipeline land in `$env:TEMP` (`TEMP` on Windows,
  `/tmp` on Unix) and are cleaned up on success and in `finally` on error. A
  `kill -9` between gzip and move can leave a partial `.sql.gz.tmp` — same window
  as the bash version.

### Core Libraries (Enhanced)

**Tools/core_lib/utils.py** — Updated for containerization:
- `get_platform_root()`: Reads `SOC_PLATFORM_ROOT` env var; falls back to dynamic detection
- `get_data_dir()`, `get_reports_dir()`, `get_archive_dir()`, `get_logs_dir()`: Safe directory creation
- Cross-platform support (Windows & Linux)

### FastAPI Service (api/main.py)

**Features:**
- Full tool registry exposure as REST endpoints
- Async job execution with polling
- Background task support
- Report download/listing
- Tool metadata and filtering
- Job history and status tracking
- Dynamic tool discovery

**Key Endpoints:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/health` | GET | Compatibility health check |
| `/api/tools` | GET | List all tools |
| `/api/tools/{name}` | GET | Get tool metadata |
| `/api/execute` | POST | Run tool asynchronously (returns job_id) |
| `/api/jobs/{job_id}` | GET | Get job status and results |
| `/api/jobs?status=running` | GET | List jobs by status |
| `/api/reports` | GET | List generated reports |
| `/api/reports/{name}` | GET | Download a report |
| `/api/registry` | GET | Get full tool registry JSON |
| `/api/registry/reload` | POST | Rebuild tool registry |
| `/docs` | GET | Swagger interactive UI |

---

## Usage Examples

### 1. List All Tools (CLI)

```bash
curl http://localhost:8000/tools | jq '.[] | {name, category, description}' | head -20
```

### 2. Get Tool Details

```bash
curl http://localhost:8000/tools/IOC_Extractor | jq .
```

### 3. Execute a Tool Asynchronously

```bash
curl -X POST http://localhost:8000/execute \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "base64_decoder",
    "arguments": {"target": "/path/to/file.txt"},
    "silent": false
  }' | jq .
```

Response:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "tool_name": "base64_decoder",
  "created_at": "2025-01-15T10:30:00",
  "completed_at": null,
  "stdout": null,
  "stderr": null,
  "exit_code": null
}
```

### 4. Poll Job Status

```bash
curl http://localhost:8000/jobs/550e8400-e29b-41d4-a716-446655440000 | jq .
```

Response (when complete):
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "completed",
  "tool_name": "base64_decoder",
  "created_at": "2025-01-15T10:30:00",
  "completed_at": "2025-01-15T10:30:05",
  "stdout": "VGhpcyBpcyBkZWNvZGVkIHRleHQ=\nDecoded: This is decoded text",
  "stderr": "",
  "exit_code": 0
}
```

### 5. List All Running Jobs

```bash
curl 'http://localhost:8000/jobs?status=running' | jq .
```

### 6. Download a Report

```bash
curl http://localhost:8000/reports | jq '.[0]'  # List reports
curl http://localhost:8000/reports/SOC_Intel_Briefing_and_Triage_Results_20250115_1030.txt \
  -o report.txt
```

---

## Data Persistence & Volumes

All data is stored in volumes for persistence across container restarts:

```yaml
volumes:
  ./Data:/app/Data           # Reports, logs, archives, workspace
  ./Playbooks:/app/Playbooks # Automated workflows
  ./Tools:/app/Tools         # Tool scripts and libraries
```

**Directories:**
- `Data/Reports/`: Generated HTML briefings and threat intel summaries
- `Data/Archive/`: Extracted IOCs, sanitized exports
- `Data/Logs/`: Commander usage logs, execution records
- `Data/Active_Workspace/`: Temporary files during processing

---

## Deployment

### Local Development

```bash
docker compose --profile api up
# Logs visible in terminal; press Ctrl+C to stop
```

### Background (Detached)

```bash
docker compose --profile api up -d
docker compose logs -f api-service
```

### Production (Single Node)

Build with production settings:

```bash
docker build -t soc-platform:1.0.0 .
docker run -d \
  --name soc-api \
  --restart unless-stopped \
  -p 8000:8000 \
  -v soc-data:/app/Data \
  -v soc-playbooks:/app/Playbooks \
  -e SOC_PLATFORM_ROOT=/app \
  soc-platform:1.0.0
```

### Kubernetes (Multi-Pod)

Use the Dockerfile as-is with a standard Deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: soc-api
spec:
  replicas: 3
  selector:
    matchLabels:
      app: soc-api
  template:
    metadata:
      labels:
        app: soc-api
    spec:
      containers:
      - name: api
        image: soc-platform:latest
        ports:
        - containerPort: 8000
        env:
        - name: SOC_PLATFORM_ROOT
          value: /app
        volumeMounts:
        - name: data
          mountPath: /app/Data
        - name: playbooks
          mountPath: /app/Playbooks
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: soc-data
      - name: playbooks
        persistentVolumeClaim:
          claimName: soc-playbooks
```

### Kubernetes + log-forwarding sidecar (task 4) — `k8s/soc-api-deployment.yaml`

The strict task-4 artifact lives in the repo as an apply-able manifest: the
FastAPI container plus a **Fluent Bit sidecar** sharing an **emptyDir**
(`/var/log/soc`). The app writes its `soc.audit` JSON stream to
`AUDIT_LOG_FILE=/var/log/soc/audit.json` (dual sink: stdout + file), the
sidecar tails that file and forwards each event **over HTTPS**
(`tls On`, `tls.verify On`) with an **`Authorization: Bearer …`** header
to an external webhook (`SOC_LOG_WEBHOOK_HOST/PORT/URI`), with the token
from a Secret.

```bash
# secrets (never committed — see the manifest header):
kubectl create secret generic soc-api-secrets \
  --from-literal=api-key='…' --from-literal=database-url='…'
kubectl create secret generic soc-log-forwarder \
  --from-literal=bearer-token='…'
# then point SOC_LOG_WEBHOOK_* at your collector and apply:
kubectl apply --dry-run=server -f k8s/soc-api-deployment.yaml
```

**Verified (2026-10-21, no cluster on the dev Mac):** kubeconform `-strict`
2/2 resources valid; the Fluent Bit config was executed with real
fluent-bit 5.1.5 — parses, `${VAR}` expansion, tails the app's JSON file,
and delivered payload + bearer header end-to-end to a local HTTPS sink
(diagnostic run; the artifact's `tls.verify On` demonstrably refuses an
untrusted CA — correct posture for a real webhook). Pinned by
`tests/test_k8s_manifest.py`.

**Verified live on a cluster (2026-10-23):** `scripts/task4_cluster_verify.py`
stands up a local kind cluster (podman provider), applies the manifest and
proves the whole path end-to-end: rollout green behind the readiness probe;
the app wrote the `AUDIT_LOG_FILE` the sidecar tails; an ungated mutation
produced the `401 auth_rejected` security event; Fluent Bit delivered it
over **HTTPS with `tls.verify On`** (private CA injected at render time
only — the checked-in manifest keeps no custom CA) to an **external
host-side sink outside the cluster**; the sink saw the `Authorization:
Bearer` value matching the Secret and the payload intact. **Still not
verified:** delivery to a webhook presenting a publicly-trusted CA (the
cluster runs used a private-CA sink) and `kubectl apply --dry-run=server`
against the production admission chain.

---

## Improvements Over CLI-Only

| Feature | CLI | API |
|---------|-----|-----|
| **Cross-Platform** | ❌ Windows only | ✅ Linux, macOS, Windows |
| **Programmatic Access** | Manual subprocess | ✅ REST + JSON |
| **Job Tracking** | None | ✅ Async polling, history |
| **Scalability** | Single instance | ✅ Multi-container, K8s |
| **Monitoring** | None | ✅ Health checks, logs |
| **Integration** | Copy-paste output | ✅ Webhook, scheduler, SOAR |
| **Data Isolation** | Fixed paths | ✅ Volume-based, multi-tenant |
| **Reproducibility** | Depends on host | ✅ Identical in all environments |

---

## Environment Variables

Set in `.env` or pass to `docker run -e`:

```env
SOC_PLATFORM_ROOT=/app                    # Platform home directory
LOG_LEVEL=INFO                            # Logging verbosity
PYTHONUNBUFFERED=1                        # Real-time logs
```

---

## Stopping Services

```bash
# Stop API
docker compose --profile api down

# Stop and remove volumes
docker compose --profile api down -v

# Stop CLI container
docker compose --profile cli down --rm
```

---

## Troubleshooting

### API not responding?

```bash
docker compose logs api-service
docker exec soc-api-service python -c "from api.main import app; print('Import OK')"
```

### Data not persisting?

```bash
docker compose ps -a          # Check volume mounts
docker volume ls              # List all volumes
docker volume inspect soc_platform_release_20260812_0943_postgres-data
```

### Tool execution failing?

```bash
docker exec soc-api-service python Tools/base64_decoder/base64_decoder.py --target "test=="
```

---

## Next Steps

1. **Webhook Integration**: Trigger tool execution from SOAR platforms (Splunk, Demisto, etc.)
2. **Authentication**: Add JWT or API key support
3. **Rate Limiting**: Protect against abuse
5. **Metrics**: Export Prometheus metrics for monitoring
6. **CLI Client**: Python CLI tool to interact with API from terminal
7. **Front-End**: React/Vue dashboard for job submission and report viewing

---

## Files Modified/Created

- ✅ **Dockerfile** — Multi-stage, ~216MB final image
- ✅ **docker-compose.yml** — API + CLI services
- ✅ **requirements.txt** — Python dependencies
- ✅ **.dockerignore** — Exclude unnecessary files
- ✅ **Tools/core_lib/utils.py** — Enhanced for containerization
- ✅ **api/main.py** — Full FastAPI service (600+ lines)
- ✅ **api/__init__.py** — Package initialization
- ✅ **scripts/start_soc_api.ps1** — Cross-platform API starter (PowerShell Core)
- ✅ **scripts/backup_db.ps1** — Cross-platform daily backup (PowerShell Core)

---

## Questions?

Refer to:
- FastAPI docs: https://fastapi.tiangolo.com/
- Docker docs: https://docs.docker.com/
- docker-compose: https://docs.docker.com/compose/

## Runtime versions (Sept 25)

- **Local dev**: Python 3.14 (`.venv314`; see `.venv314/` README note in services/mock_splunk)
- **Docker**: `python:3.14-slim` (Dockerfile)
- **Vercel (production)**: **retired Sept 30, 2026** (owner decision —
  `deploy.yml` deleted, no deploys anymore). The old guidance applied while
  it lived: `vercel build` reads a root `.python-version`, and adding `3.14`
  broke 8 consecutive deploys starting at commit `00c7e69` (see GitHub
  Actions history). The root pin is still deliberately absent (GAP_AUDIT
  F-note, `09a53e5`) — and with no deploys the concern is moot; if hosted
  preview ever returns, verify the runtime on a preview deploy first.
