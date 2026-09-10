#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATABASE_URL="${DATABASE_URL:-postgresql+psycopg://soc_platform@localhost:5433/soc_platform}"
export DATABASE_URL
COMPOSE_DATABASE_URL="${COMPOSE_DATABASE_URL:-postgresql+psycopg://soc_platform@postgres:5432/soc_platform}"
export COMPOSE_DATABASE_URL

echo "[*] Syncing shared logic from repo files into database..."
echo "[*] DATABASE_URL=${DATABASE_URL}"

IMPORTER="${REPO_ROOT}/Tools/es_rules_importer/es_rules_importer.py"
if [[ ! -f "${IMPORTER}" ]]; then
  echo "[!] Rules importer not found: ${IMPORTER}" >&2
  exit 1
fi

import_if_present() {
  local file="$1"
  if [[ -f "${file}" ]]; then
    docker compose run --rm -T --no-deps \
      -e "DATABASE_URL=${COMPOSE_DATABASE_URL}" api-service \
      python "${IMPORTER#"${REPO_ROOT}/"}" --import "${file#"${REPO_ROOT}/"}"
  fi
}

import_if_present "${REPO_ROOT}/sample_rules.json"
import_if_present "${REPO_ROOT}/supportive_rules.json"
import_if_present "${REPO_ROOT}/local-backups/shared-logic-export/supportive_rules.exported.json"

if [[ -f "${REPO_ROOT}/placeholder_aliases.json" ]]; then
  docker compose run --rm -T --no-deps \
    -e "DATABASE_URL=${COMPOSE_DATABASE_URL}" api-service \
    python scripts/import_placeholder_aliases.py --input placeholder_aliases.json
fi
if [[ -f "${REPO_ROOT}/local-backups/shared-logic-export/placeholder_aliases.exported.json" ]]; then
  docker compose run --rm -T --no-deps \
    -e "DATABASE_URL=${COMPOSE_DATABASE_URL}" api-service \
    python scripts/import_placeholder_aliases.py \
    --input local-backups/shared-logic-export/placeholder_aliases.exported.json
fi

echo "[+] Shared logic sync complete"
