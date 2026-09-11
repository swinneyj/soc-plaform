#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8001}"
PYTHON_BIN="${PYTHON_BIN:-${REPO_ROOT}/.venv/bin/python}"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "[!] Virtualenv Python not found at ${PYTHON_BIN}" >&2
  echo "    Create/activate the project virtualenv first." >&2
  exit 1
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "[!] DATABASE_URL is not set." >&2
  echo "    Example: export DATABASE_URL='postgresql+psycopg://mini:<password>@localhost:5432/soc_platform'" >&2
  exit 1
fi

export OLLAMA_URL="${OLLAMA_URL:-http://127.0.0.1:11434}"
export OLLAMA_NUM_PREDICT="${OLLAMA_NUM_PREDICT:-500}"
export OLLAMA_TIMEOUT="${OLLAMA_TIMEOUT:-90}"
export CORS_ORIGINS="${CORS_ORIGINS:-https://soc-plaform-git-justin-swinneyjs-projects.vercel.app}"

echo "[*] Restarting SOC Platform API on ${API_HOST}:${API_PORT}..."

existing_pids="$(lsof -tiTCP:"${API_PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${existing_pids}" ]]; then
  echo "[*] Stopping existing API process(es): ${existing_pids//$'\n'/ }"
  while read -r pid; do
    [[ -n "${pid}" ]] && kill "${pid}"
  done <<< "${existing_pids}"
fi

for attempt in {1..30}; do
  if ! lsof -tiTCP:"${API_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if lsof -tiTCP:"${API_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[!] Port ${API_PORT} is still in use; refusing to start a duplicate API." >&2
  exit 1
fi

echo "[+] Starting API. Cloudflared can continue pointing at http://127.0.0.1:${API_PORT}"
exec "${PYTHON_BIN}" -m uvicorn --app-dir "${REPO_ROOT}" api.main:app --host "${API_HOST}" --port "${API_PORT}"
