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

compose run --rm test security

if compose run --rm test python -m bandit --version >/dev/null 2>&1; then
  compose run --rm test python -m bandit -r . -c /app/pyproject.toml
else
  echo "bandit is not installed in the current test image; Poetry security group must be installed before enabling this check." >&2
fi

if compose run --rm test python -m pip_audit --version >/dev/null 2>&1; then
  compose run --rm test python -m pip_audit
else
  echo "pip-audit is not installed in the current test image; Poetry security group must be installed before enabling this check." >&2
fi
