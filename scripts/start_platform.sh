#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

POSTGRES_HOST_PORT="${POSTGRES_HOST_PORT:-5433}"
API_HOST_PORT="${API_HOST_PORT:-8000}"
export POSTGRES_HOST_PORT API_HOST_PORT
export COMPOSE_DATABASE_URL="${COMPOSE_DATABASE_URL:-postgresql+psycopg://soc_platform@postgres:5432/soc_platform}"
export DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://soc_platform@localhost:${POSTGRES_HOST_PORT}/soc_platform}"

echo "============================================"
echo "  Starting SOC Platform"
echo "============================================"
echo "[*] Repo root: ${REPO_ROOT}"

if ! docker version >/dev/null 2>&1; then
  echo "[!] Docker is not running or this user cannot access the Docker socket." >&2
  exit 1
fi

echo "[*] Starting postgres and redis via docker compose..."
docker compose up -d postgres redis

echo "[*] Waiting for PostgreSQL to become ready..."
for attempt in $(seq 1 60); do
  if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER:-soc_platform}" \
      -d "${POSTGRES_DB:-soc_platform}" >/dev/null 2>&1; then
    break
  fi
  if [[ "${attempt}" == 60 ]]; then
    echo "[!] PostgreSQL did not become ready in time." >&2
    exit 1
  fi
  sleep 2
done

SHARED_DUMP_DIR="${SHARED_DUMP_DIR:-${REPO_ROOT}/local-backups/shared-db}"
SHARED_DUMP_PATH="${SHARED_DUMP_DIR}/current_soc_platform_dump.sql"
if [[ "${SKIP_SHARED_DUMP_RESTORE:-0}" != "1" && -s "${SHARED_DUMP_PATH}" ]]; then
  echo "[*] Restoring shared dump from ${SHARED_DUMP_PATH}"
  docker compose exec -T postgres psql -U "${POSTGRES_USER:-soc_platform}" \
    -d "${POSTGRES_DB:-soc_platform}" < "${SHARED_DUMP_PATH}"
else
  echo "[*] No shared dump found. Skipping restore."
fi

echo "[*] Building the API image for database sync and startup..."
docker compose build api-service
bash "${REPO_ROOT}/scripts/sync_shared_logic_to_db.sh"

echo "[*] Starting api-service via docker compose..."
docker compose up -d --build api-service

echo "[*] Waiting for API health at http://127.0.0.1:${API_HOST_PORT}/health"
for attempt in $(seq 1 60); do
  if curl --fail --silent "http://127.0.0.1:${API_HOST_PORT}/health" >/dev/null 2>&1; then
    echo "[+] API is healthy"
    echo "[+] DATABASE_URL=${DATABASE_URL}"
    exit 0
  fi
  if [[ "${attempt}" == 60 ]]; then
    echo "[!] API did not become healthy in time. Check: docker compose logs api-service" >&2
    exit 1
  fi
  sleep 2
done
