#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# PhotoBox Enterprise Test Runner
#
# Usage (via Docker Compose):
#   docker compose run --rm test unit       — fast unit + security tests
#   docker compose run --rm test api        — API integration tests
#   docker compose run --rm test celery     — Celery pipeline tests
#   docker compose run --rm test security   — security-only tests
#   docker compose run --rm test cloudinary — Cloudinary integration (needs creds)
#   docker compose run --rm test e2e        — End-to-end photographer flow
#   docker compose run --rm test coverage   — Full suite with coverage report
#   docker compose run --rm test all        — Everything
# ─────────────────────────────────────────────────────────────────────────────
set -e

SUITE="${1:-unit}"
if [ "$SUITE" = "pytest" ] || [ "$SUITE" = "python" ]; then
  echo "[INFO] Raw command passthrough: $*"
  exec "$@"
fi

shift 1 || true
EXTRA_ARGS="$@"

echo "================================================================"
echo "  PhotoBox Enterprise Test Runner"
echo "  Suite    : $SUITE"
echo "  Extra    : $EXTRA_ARGS"
echo "  Workdir  : $(pwd)"
echo "================================================================"

# Clean stale bytecode — prevents ghost failures from cached .pyc files
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true

echo "[INFO] Python  : $(python --version)"
echo "[INFO] Pytest  : $(python -m pytest --version)"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Wait for database to be ready before running any tests
# ─────────────────────────────────────────────────────────────────────────────
echo "[INFO] Waiting for database..."
python -c "
import os, sys, time, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings')
django.setup()
from django.db import connections
from django.db.utils import OperationalError
for i in range(30):
    try:
        connections['default'].ensure_connection()
        print('[INFO] Database is ready.')
        sys.exit(0)
    except OperationalError:
        print(f'[INFO] DB not ready, retry {i+1}/30...')
        time.sleep(2)
print('[ERROR] Database not available after 60s.')
sys.exit(1)
"

# Run migrations to ensure schema is current
echo "[INFO] Running migrations..."
TMP_ROOT="${TMPDIR:-/var/tmp}"
mkdir -p "$TMP_ROOT"
MIGRATE_LOG="$TMP_ROOT/photobox-migrate-$$.log"
if ! python manage.py migrate --run-syncdb --no-input >"$MIGRATE_LOG" 2>&1; then
  tail -20 "$MIGRATE_LOG"
  rm -f "$MIGRATE_LOG"
  exit 1
fi
tail -5 "$MIGRATE_LOG"
rm -f "$MIGRATE_LOG"

echo ""

case "$SUITE" in

  # ── UNIT: fast, no external services ─────────────────────────────────────
  unit)
    echo "[RUNNING] Unit + integration tests (all app domains)..."
    exec python -m pytest \
      core/tests/ \
      gallery/tests/ \
      billing/tests/ \
      user/tests/ \
      checkout/tests/ \
      ingestion/tests/ \
      tests/ \
      --timeout=60 -v --tb=short \
      --ignore=tests/test_cloudinary_integration.py \
      --ignore=tests/test_e2e_photographer_flow.py \
      $EXTRA_ARGS
    ;;

  # ── API: HTTP integration tests ───────────────────────────────────────────
  api)
    echo "[RUNNING] API integration tests..."
    exec python -m pytest \
      gallery/tests/ \
      billing/tests/ \
      user/tests/ \
      tests/test_api_upload.py \
      --timeout=60 -v --tb=short $EXTRA_ARGS
    ;;

  # ── SECURITY: IDOR, tenant isolation, webhook, OAuth ─────────────────────
  security)
    echo "[RUNNING] Security test suite..."
    exec python -m pytest \
      gallery/tests/test_tenant_isolation.py \
      gallery/tests/test_storage_unit.py \
      gallery/tests/test_presigned_url_security.py \
      gallery/tests/test_model.py \
      gallery/tests/test_download_authorization.py \
      gallery/tests/test_asset_hardening.py \
      billing/tests/test_security.py \
      billing/tests/test_audit_immutability.py \
      core/tests/test_auth_jwt.py \
      user/tests/test_social_adapter.py \
      user/tests/test_user_api.py \
      ingestion/tests/test_r2_webhook.py \
      --timeout=60 -v --tb=short $EXTRA_ARGS
    ;;

  # ── CELERY: task pipeline tests ───────────────────────────────────────────
  celery)
    echo "[RUNNING] Celery pipeline tests..."
    exec python -m pytest \
      tests/test_celery_tasks.py \
      --timeout=60 -v --tb=short $EXTRA_ARGS
    ;;

  # ── CLOUDINARY: requires real credentials ─────────────────────────────────
  cloudinary)
    echo "[RUNNING] Cloudinary integration tests (requires real creds)..."
    exec python -m pytest \
      tests/test_cloudinary_integration.py \
      -m cloudinary --timeout=120 -v -s $EXTRA_ARGS
    ;;

  # ── E2E: full photographer flow ───────────────────────────────────────────
  e2e)
    echo "[RUNNING] End-to-end photographer flow..."
    exec python -m pytest \
      tests/test_e2e_photographer_flow.py \
      -m e2e --timeout=300 -v -s $EXTRA_ARGS
    ;;

  # ── COVERAGE: full suite with HTML report ────────────────────────────────
  coverage)
    echo "[RUNNING] Coverage analysis..."
    exec python -m pytest \
      core/tests/ \
      gallery/tests/ \
      billing/tests/ \
      user/tests/ \
      checkout/tests/ \
      ingestion/tests/ \
      tests/ \
      --cov=. \
      --cov-report=term-missing \
      --cov-report=html:htmlcov \
      --cov-fail-under=70 \
      --timeout=120 -v --tb=short \
      --ignore=tests/test_cloudinary_integration.py \
      --ignore=tests/test_e2e_photographer_flow.py \
      $EXTRA_ARGS
    ;;

  # ── ALL: everything including integration ────────────────────────────────
  all)
    echo "[RUNNING] Full system suite..."
    exec python -m pytest \
      core/tests/ \
      gallery/tests/ \
      billing/tests/ \
      user/tests/ \
      checkout/tests/ \
      ingestion/tests/ \
      tests/ \
      --timeout=180 -v --tb=short $EXTRA_ARGS
    ;;

  # ── FLAKE8: linting ──────────────────────────────────────────────────────
  flake8)
    echo "[RUNNING] flake8 lint check (max-line-length=100, migrations excluded)..."
    exec python -m flake8 \
      --config /app/.flake8 \
      --count \
      --statistics \
      . $EXTRA_ARGS
    ;;

  *)
    echo "ERROR: Unknown suite '$SUITE'"
    echo ""
    echo "Usage: docker compose run --rm test [SUITE]"
    echo ""
    echo "Suites:"
    echo "  unit       — All unit + integration tests (default, fast)"
    echo "  api        — API integration tests"
    echo "  security   — Security-focused tests (IDOR, webhook, OAuth)"
    echo "  celery     — Celery task pipeline tests"
    echo "  cloudinary — Cloudinary integration (requires real credentials)"
    echo "  e2e        — End-to-end photographer flow"
    echo "  coverage   — Full suite with HTML coverage report"
    echo "  flake8     — Lint the codebase"
    echo "  all        — Everything"
    exit 1
    ;;
esac
