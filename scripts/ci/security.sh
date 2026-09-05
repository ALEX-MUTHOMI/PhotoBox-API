#!/usr/bin/env bash
# Fail-closed security gates: missing bandit/pip-audit is a red build, not a skip.
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

# Fail closed: tools must be present in the CI/test image.
if ! compose run --rm test python -m bandit --version >/dev/null 2>&1; then
  echo "bandit is required in the test image (Poetry security group)." >&2
  exit 1
fi
compose run --rm test python /scripts/ci/bandit_redacted.py -r . -c /repo-config/pyproject.toml

if ! compose run --rm test python -m pip_audit --version >/dev/null 2>&1; then
  echo "pip-audit is required in the test image (Poetry security group)." >&2
  exit 1
fi
# Do not wrap pip-audit in retries — findings are deterministic, not flakes.
compose run --rm test python -m pip_audit

# Optional fortress hook: set RUN_ZAP_PASSIVE=1 to also run OWASP ZAP passive.
if [ "${RUN_ZAP_PASSIVE:-0}" = "1" ]; then
  bash scripts/ci/zap_passive.sh
fi
