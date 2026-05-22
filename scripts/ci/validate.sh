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

compose config -q
compose -f docker-compose-deploy.yml config -q

if command -v poetry >/dev/null 2>&1; then
  poetry --version
  poetry check
  if [ ! -f poetry.lock ]; then
    echo "poetry.lock is missing; generate it with Poetry before enabling deterministic Poetry CI." >&2
    exit 1
  fi
  poetry lock --check
else
  echo "Poetry is not installed in this environment." >&2
  if [ -f pyproject.toml ] && [ ! -f poetry.lock ]; then
    echo "pyproject.toml exists but poetry.lock is missing." >&2
    exit 1
  fi
fi

python scripts/ci/env_sanity.py
