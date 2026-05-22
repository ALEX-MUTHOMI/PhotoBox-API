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

compose run --rm test python manage.py check
compose run --rm test python manage.py makemigrations --check --dry-run
compose run --rm test python manage.py showmigrations --plan
compose run --rm test python manage.py shell -c "import django; django.setup(); print('django ok')"
compose run --rm test python manage.py shell -c "import app.urls; print('urls ok')"
compose run --rm test python manage.py shell -c "from celery import current_app; print(current_app.main)"
compose run --rm test python manage.py shell -c "from django.urls import reverse; print(reverse('health-check')); print(reverse('health'))"
compose run --rm test python -m pytest /repo-tests/smoke -v --tb=short --timeout=30
