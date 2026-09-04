#!/usr/bin/env bash
# Newman Kenya contracts against Docker Compose app. Never hits Daraja.
set -euo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"

# Fail hard if collection mentions Daraja (STK must stay out of scanners).
if grep -qi 'daraja' postman/kenya.postman_collection.json postman/kenya.local.postman_environment.json; then
  echo "[Newman] Collection must not mention daraja." >&2
  exit 1
fi

compose() {
  local env_file="${COMPOSE_SAFE_ENV_FILE:-.env.example}"
  if [ ! -f "$env_file" ]; then
    env_file="/dev/null"
  fi
  local docker_bin="docker"
  if command -v docker.exe >/dev/null 2>&1; then
    docker_bin="docker.exe"
  fi
  if "$docker_bin" compose version >/dev/null 2>&1; then
    "$docker_bin" compose --env-file "$env_file" "$@"
    return
  fi
  echo "docker compose not found" >&2
  exit 1
}

echo "================================================================"
echo "  Newman Kenya contracts (no Daraja)"
echo "================================================================"

compose up -d db redis
compose up -d --no-build app-dast || compose up -d app-dast

echo "[Newman] Waiting for app health..."
for i in $(seq 1 40); do
  if compose exec -T app-dast python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health-check/', timeout=3)" >/dev/null 2>&1; then
    echo "[Newman] App is healthy."
    break
  fi
  if [ "$i" -eq 40 ]; then
    echo "[Newman] App failed to become healthy." >&2
    compose logs --tail=80 app-dast || true
    exit 1
  fi
  sleep 3
done

# Optional seeder for a real share_code (DEBUG/DAST only).
compose exec -T app-dast python manage.py seed_kenya_newman || true

echo "[Newman] Running collection..."
set +e
compose --profile newman run --rm --no-deps newman \
  run /etc/newman/kenya.postman_collection.json \
  -e /etc/newman/kenya.local.postman_environment.json \
  --reporters cli \
  --timeout-request 30000
NEWMAN_RC=$?
set -e

echo "[Newman] exit code: ${NEWMAN_RC}"
exit "${NEWMAN_RC}"
