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

docker build .
compose build
