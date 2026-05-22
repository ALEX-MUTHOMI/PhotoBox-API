#!/usr/bin/env bash
set -eo pipefail

cd "$(dirname "$0")/../.."
compose() {
  local env_file="${COMPOSE_SAFE_ENV_FILE:-.env.example}"
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose --env-file "$env_file" "$@"
  else
    docker compose --env-file "$env_file" "$@"
  fi
}

compose run --rm test python -m pytest /repo-tests/integration --ds=app.settings -v --tb=short --timeout=60 -o cache_dir=/home/django-user/.pytest-cache "$@"
