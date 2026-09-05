#!/usr/bin/env bash
# Fast-fail lint + security on the GitHub runner (no Compose / no image wait).
# Flake8 uses the repo-root .flake8 contract (same policy as app/.flake8).
set -eo pipefail

cd "$(dirname "$0")/../.."

python scripts/ci/secret_hygiene.py

poetry run flake8 --config=.flake8 --count --statistics app scripts tests

poetry run bandit -r app -c pyproject.toml -ll -ii

# Audit runtime dependencies only (not lint/test tooling). Fail closed.
poetry self add poetry-plugin-export >/dev/null 2>&1 || true
poetry export --without-hashes --only main -o /tmp/photobox-requirements-audit.txt
pip-audit -r /tmp/photobox-requirements-audit.txt --desc
