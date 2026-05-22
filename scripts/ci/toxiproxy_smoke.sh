#!/usr/bin/env bash
set -eo pipefail

cd "$(dirname "$0")/../.."
compose() {
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    docker compose "$@"
  fi
}

compose -f docker-compose.yml -f docker-compose.toxiproxy.yml config -q
compose -f docker-compose.yml -f docker-compose.toxiproxy.yml run --rm test python -m pytest /repo-tests/resilience -v --tb=short --timeout=30 "$@"
