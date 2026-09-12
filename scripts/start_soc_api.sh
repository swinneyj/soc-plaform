#!/usr/bin/env bash
# SOC Platform self-start script (used by LaunchAgent and by humans).
# Order: wait for Postgres -> start Ollama if absent -> start API (uvicorn).
# Safe to run repeatedly: exits quietly if the API port is already served.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_PORT="${SOC_API_PORT:-8000}"
API_HOST="${SOC_API_HOST:-127.0.0.1}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"

# Config + virtualenv: prefer the canonical repo copy; fall back to the
# original Downloads checkout where .env/.venv lived first.
ENV_FILE="${SOC_ENV_FILE:-$REPO_ROOT/.env}"
[[ -f "$ENV_FILE" ]] || ENV_FILE="$HOME/Downloads/soc-plaform-main/.env"
VENV_PY="${SOC_VENV_PY:-$REPO_ROOT/.venv/bin/python}"
[[ -x "$VENV_PY" ]] || VENV_PY="$HOME/Downloads/soc-plaform-main/.venv/bin/python"

LOG_DIR="$REPO_ROOT/logs"
mkdir -p "$LOG_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[$(date '+%F %T')] ERROR: no .env found (tried $REPO_ROOT/.env and Downloads fallback)" >> "$LOG_DIR/soc-api.log"
  exit 1
fi
if [[ ! -x "$VENV_PY" ]]; then
  echo "[$(date '+%F %T')] ERROR: no venv python found (tried $REPO_ROOT/.venv and Downloads fallback)" >> "$LOG_DIR/soc-api.log"
  exit 1
fi

# 1) Wait up to 60s for PostgreSQL (brew service starts it at login, but races us)
for i in $(seq 1 60); do
  if nc -z 127.0.0.1 5432 >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! nc -z 127.0.0.1 5432 >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] ERROR: Postgres not listening on 5432 after 60s" >> "$LOG_DIR/soc-api.log"
  exit 1
fi

# 2) Start Ollama if it is not already serving
if ! nc -z 127.0.0.1 "$OLLAMA_PORT" >/dev/null 2>&1; then
  OLLAMA_BIN="$(command -v ollama || echo /usr/local/bin/ollama)"
  if [[ -x "$OLLAMA_BIN" ]]; then
    nohup "$OLLAMA_BIN" serve >> "$LOG_DIR/ollama.log" 2>&1 &
    echo "[$(date '+%F %T')] started ollama serve (pid $!)" >> "$LOG_DIR/soc-api.log"
  else
    echo "[$(date '+%F %T')] WARN: ollama binary not found; AI analysis will be unavailable" >> "$LOG_DIR/soc-api.log"
  fi
fi

# 3) Start the API unless the port is already served (another instance won)
if nc -z 127.0.0.1 "$API_PORT" >/dev/null 2>&1; then
  echo "[$(date '+%F %T')] API port $API_PORT already served; nothing to do" >> "$LOG_DIR/soc-api.log"
  sleep 30   # throttles launchd KeepAlive relaunches if we ever get here repeatedly
  exit 0
fi

echo "[$(date '+%F %T')] starting uvicorn on $API_HOST:$API_PORT" >> "$LOG_DIR/soc-api.log"
cd "$REPO_ROOT"
set -a
source "$ENV_FILE"
set +a
exec "$VENV_PY" -m uvicorn --app-dir "$REPO_ROOT" api.main:app --host "$API_HOST" --port "$API_PORT"
