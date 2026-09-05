#!/usr/bin/env bash
# Fast-fail lint + security on the GitHub runner (no Compose / no image wait).
set -eo pipefail

cd "$(dirname "$0")/../.."

python scripts/ci/secret_hygiene.py

poetry run flake8 \
  --max-line-length=100 \
  --exclude='*/migrations/*,.venv,photobox-docs-site,node_modules,artifacts' \
  --count \
  --statistics \
  app scripts tests

poetry run bandit -r app -c pyproject.toml -ll -ii

# Export for pip-audit without hashes; findings fail the job (no retry).
poetry self add poetry-plugin-export >/dev/null 2>&1 || true
poetry export --without-hashes --with security,lint,test,dev -o /tmp/photobox-requirements-audit.txt
pip-audit -r /tmp/photobox-requirements-audit.txt --desc
