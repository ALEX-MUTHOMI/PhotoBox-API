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

python scripts/ci/secret_hygiene.py

compose run --rm test security

if compose run --rm test python -m bandit --version >/dev/null 2>&1; then
  compose run --rm test python /scripts/ci/bandit_redacted.py -r . -c /repo-config/pyproject.toml
else
  echo "bandit is not installed in the current test image; Poetry security group must be installed before enabling this check." >&2
fi

if compose run --rm test python -m pip_audit --version >/dev/null 2>&1; then
  compose run --rm test python -m pip_audit
else
  echo "pip-audit is not installed in the current test image; Poetry security group must be installed before enabling this check." >&2
fi
