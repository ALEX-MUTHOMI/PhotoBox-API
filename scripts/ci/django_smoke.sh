#!/bin/sh

set -eu

echo "[smoke] starting Django smoke gate"

# Never inherit arbitrary shell-level settings from the developer host or the
# generic test service. This gate must exercise production-like Django startup.
export DEBUG="0"
export TESTING="0"
export SECRET_KEY="${SECRET_KEY:-ci-smoke-secret-key-not-for-production-0123456789-abcdefghijklmnopqrstuvwxyz-9876543210}"
export JWT_SIGNING_KEY="${JWT_SIGNING_KEY:-test-signing-key-that-is-at-least-32-bytes-long!!}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-app.settings}"
export ALLOWED_HOSTS="${ALLOWED_HOSTS:-localhost,127.0.0.1}"

# Provide safe placeholders for optional integrations so imports stay deterministic.
export CLOUDFLARE_R2_ENDPOINT="${CLOUDFLARE_R2_ENDPOINT:-https://stub.r2.cloudflarestorage.com}"
export CLOUDFLARE_R2_BUCKET_NAME="${CLOUDFLARE_R2_BUCKET_NAME:-ci-test-bucket}"
export CLOUDFLARE_ACCESS_KEY_ID="${CLOUDFLARE_ACCESS_KEY_ID:-ci-access-key}"
export CLOUDFLARE_SECRET_ACCESS_KEY="${CLOUDFLARE_SECRET_ACCESS_KEY:-ci-secret-key}"
export CLOUDFLARE_WEBHOOK_SECRET="${CLOUDFLARE_WEBHOOK_SECRET:-ci-webhook-secret}"
export CLOUDINARY_CLOUD_NAME="${CLOUDINARY_CLOUD_NAME:-ci-cloud}"
export LEMON_SQUEEZY_API_KEY="${LEMON_SQUEEZY_API_KEY:-ci-ls-api-key}"
export LEMON_SQUEEZY_STORE_ID="${LEMON_SQUEEZY_STORE_ID:-1}"
export LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY="${LEMON_SQUEEZY_WEBHOOK_SECRET_PRIMARY:-ci-ls-primary-secret}"
export FRONTEND_URL="${FRONTEND_URL:-http://localhost:3000}"
export CORS_ALLOWED_ORIGINS="${CORS_ALLOWED_ORIGINS:-http://localhost:3000}"

python --version

echo "[smoke] django check"
python manage.py check

echo "[smoke] django deploy check"
python manage.py check --deploy

echo "[smoke] migrations drift"
python manage.py makemigrations --check --dry-run
python manage.py showmigrations

echo "[smoke] urlconf import"
python manage.py shell -c "import importlib; importlib.import_module('app.urls'); print('urls-ok')"

echo "[smoke] celery import"
python manage.py shell -c "from app.celery import app as celery_app; print(celery_app.main)"

echo "[smoke] health endpoint"
python manage.py shell -c "from django.test import Client; r=Client(HTTP_HOST='localhost').get('/api/health-check/', secure=True, HTTP_X_FORWARDED_PROTO='https'); print(r.status_code); assert r.status_code == 200"

echo "[smoke] invalid DEBUG handling"
TMP_OUT="${PWD}/.smoke-debug-invalid.$$"
if env DEBUG=release python manage.py check >"$TMP_OUT" 2>&1; then
  cat "$TMP_OUT"
  rm -f "$TMP_OUT"
  echo "[smoke] expected DEBUG=release to fail clearly"
  exit 1
fi
grep -q "Invalid boolean value for DEBUG" "$TMP_OUT"
rm -f "$TMP_OUT"

echo "[smoke] completed"
