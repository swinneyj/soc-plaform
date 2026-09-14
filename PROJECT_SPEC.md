# SOC Platform - Project Specification for AI Agents

## Overview
Local SOC orchestration platform with containerized tools, PostgreSQL runtime database, optional legacy SQLite fallback when separately provided, Ollama AI integration, and web dashboard.

## Architecture

### Directory Structure
```
project-root/
├── api/
│   └── main.py                 # FastAPI server (port 8000)
├── web/
│   └── index.html             # Vue.js dashboard UI
├── db/
│   └── models.py              # SQLAlchemy ORM models
├── scripts/
│   ├── bootstrap_new_user.ps1 # New-user bootstrap and dump restore
│   ├── start_platform.ps1     # Standard platform startup
│   ├── stop_platform.ps1      # Standard platform shutdown and export
│   └── sync_shared_logic_to_db.ps1 # Repo-backed rules/query sync
├── services/
│   └── ollama_service.py       # Ollama LLM client wrapper
├── Tools/
│   ├── tool_indexer/          # Registry builder
│   ├── splunk_csv_ingestor/   # CSV import tool
│   ├── splunk_folder_watcher/ # Auto-watch folder tool
│   ├── closure_notes_generator/ # Incident closure templates
│   └── [~30+ other tools]     # SOC analysis/response tools (see Commander_Registry.json; count varies)
├── docker-compose.yml         # Service orchestration
└── Dockerfile                 # Container build (multi-stage)
```

### Key Services
- **API** (FastAPI): REST endpoints for tools, database, Ollama integration
- **Redis**: Job queue for long-running tasks
- **Ollama**: Local LLM inference (on host network via `host.docker.internal:11434`)
- **PostgreSQL**: Default runtime database for events and cases
- **SQLite**: Optional legacy fallback only if a backup file is provided separately

## Database Schema

### SplunkEvent
Stores raw Splunk/security events
```
id (PK), sourcetype, source, host, raw (text), timestamp, created_at
```

### TriageResult
Stores analyzed/triaged cases
```
case_id (PK), rule_name, rule_id, verdict, confidence_score,
analysis_summary, remediation_steps, triaged_at
```

## API Endpoints

### Tools
- `GET /api/tools` - List all tools with metadata (registry count, ~30-40)
- `GET /api/tools/{name}` - Get specific tool info
- `POST /api/execute` - Execute tool asynchronously (returns job_id)
- `GET /api/jobs/{job_id}` - Poll job status/results
- `POST /api/registry/reload` - Rebuild tool registry

### Database
- `GET /api/db/stats` - Database statistics
- `GET /api/db/triage` - List triaged cases (limit=50)
- `GET /api/db/triage/{case_id}` - Get specific case
- `POST /api/db/analyze` - AI analysis using Ollama (POST body: `{case_id, model, context}`)
- `GET /api/db/ollama/health` - Check Ollama availability + list models

### Execution
- `POST /api/execute` - Run tool (request body: `{tool_name, arguments: {key: val}, silent: bool}`)
- Returns: `{job_id, status, tool_name, created_at, ...}`

## Tool Registry

Tools are indexed from `/Tools/` directory into `Commander_Registry.json`:
```json
{
  "name": "tool_name",
  "file_name": "tool_name.py",
  "category": "Analysis",
  "description": "What it does",
  "path": "/app/Tools/category/tool_name.py",
  "arguments": [
    {"name": "arg1", "type": "string", "required": true}
  ]
}
```

To add a new tool:
1. Create `/Tools/category/tool_name/tool_name.py`
2. Implement `main()` with argparse
3. Run registry rebuild: `docker exec soc-api-service python /app/Tools/tool_indexer/tool_indexer.py`
4. Tool auto-appears in API and Dashboard

## Web Dashboard (Vue.js)

### Tabs
- **Tools**: Browse and execute all tools from registry
- **Database**: View Splunk events + Triage cases
- **AI Analysis**: Select case + model, run Ollama analysis
- **Status**: System health, Ollama models, job queue

### Key Features
- Real-time job polling (`/api/jobs/{id}`)
- Model selector (qwen3.5:2b, qwen3.5:9b, qwen2.5:14b, llama3.1:8b)
- CSV export for cases/events
- Dark theme

## Data Flow

### Ingestion
```
Splunk Export (CSV)
  → splunk_folder_watcher (watches Splunk_Exports/)
  → splunk_csv_ingestor (imports to SplunkEvent table)
  → active database backend (default: PostgreSQL)
  → Dashboard (Database tab)
```

### Analysis
```
User selects case in Dashboard
  → POST /api/db/analyze {case_id, model}
  → API queries SplunkEvent from DB
  → Sends to Ollama via ollama_service
  → Returns AI-generated analysis
  → Displays in UI
```

### Tool Execution
```
User clicks "Execute" on a tool
  → POST /api/execute {tool_name, arguments}
  → Background task runs tool subprocess
  → Stores result in jobs dict
  → Frontend polls /api/jobs/{id} for status
  → Results displayed when done
```

## Configuration

### Environment Variables
```
OLLAMA_URL=http://host.docker.internal:11434  # Default
DATABASE_URL=postgresql+psycopg://soc_platform@postgres:5432/soc_platform
REGISTRY_PATH=/app/Commander_Registry.json
REPORTS_DIR=/app/reports
SPLUNK_EXPORTS_DIR=/app/Splunk_Exports
```

### Docker Compose Runtime
```
docker compose up -d postgres         # PostgreSQL only
docker compose up -d api-service redis postgres
docker compose down                   # Stop all
```

## Authentication & Security
- **Ollama**: Local-only (no auth, host network)
- **API**: No auth (local network only)
- **Database**: PostgreSQL by default; SQLite retained for backup/export migration paths
- **Splunk Integration**: Manual CSV export (smartcard auth in browser)

## Adding New Features

### New Tool
1. Create script in `/Tools/category/tool_name/tool_name.py`
2. Implement with argparse
3. Test: `docker exec soc-api-service python /app/Tools/category/tool_name/tool_name.py --help`
4. Rebuild registry (auto on compose restart)

### New Database Table
1. Add model to `/db/models.py` (SQLAlchemy class)
2. Add endpoint to `/api/main.py`
3. Restart API

### Database Bootstrap For New Users
1. Clone the repo
2. Start PostgreSQL locally or via compose
3. Restore a PostgreSQL dump or migrate the preserved SQLite backup with `/scripts/migrate_sqlite_to_postgres.py`
4. Set `DATABASE_URL`
5. Start the API

### New Dashboard Feature
1. Add Vue method to `/web/index.html`
2. Call API endpoint from method
3. Update HTML/styling as needed
4. Refresh browser

### New LLM Integration
1. Extend `/services/ollama_service.py`
2. Add method for new model/API
3. Call from analysis endpoint

## Development Workflow

```bash
# Local changes
cd project-root
# Edit files

# Rebuild image
docker build -t soc-platform:latest .

# Restart services
docker compose --profile api up -d

# Test
curl http://localhost:8000/api/tools

# View logs
docker compose logs -f api-service
```

## Extending for Another AI Agent

### What to Share
1. **This spec file** (you're reading it)
2. **Database schema** (SQL exports of table definitions)
3. **API docs** (generated at `/docs` or OpenAPI JSON)
4. **Tool template** (example tool structure)
5. **Architecture diagram** (text or image)

### What NOT to Share
- Individual tool source code (share specs instead)
- Internal service implementations (share interfaces)
- Full file contents (share logic + structure)

### Example Remote Tool Development
Agent has this spec, can build tool locally:
```python
# Tools/custom_category/custom_tool/custom_tool.py
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', default='result.txt')
    args = parser.parse_args()
    
    # Do work
    result = process(args.input)
    
    print(f"[+] Completed: {result}")
    with open(args.output, 'w') as f:
        f.write(result)

if __name__ == "__main__":
    main()
```

Agent sends this file, you:
1. Place it in your `/Tools/custom_category/custom_tool/`
2. Rebuild registry
3. Tool appears in dashboard immediately

## Known Limitations
- Ollama must be running on host (not in container)
- SQLite not suitable for multi-node deployments
- Smartcard auth requires manual Splunk CSV export
- Tools execute synchronously (max 300s timeout)

## Future Enhancements
- [ ] Real-time Splunk webhook integration (requires auth)
- [ ] Database backup/export endpoints
- [ ] Multi-model ensemble analysis
- [ ] Tool dependency management
- [ ] Incident correlation engine
- [ ] Automated playbook execution
