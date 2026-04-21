#!/bin/sh
set -e

SUITE="${1:-unit}"
shift 1 || true
EXTRA_ARGS="$@"

echo "================================================================"
echo "  PhotoBox Enterprise Test Runner"
echo "  Suite    : $SUITE"
echo "  Extra    : $EXTRA_ARGS"
echo "  Workdir  : $(pwd)"
echo "================================================================"

# Clean stale bytecode to prevent ghost failures
find . -name "*.pyc" -delete 2>/dev/null || true
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true
rm -rf .pytest_cache 2>/dev/null || true

echo "[INFO] Python  : $(python --version)"
echo "[INFO] Pytest  : $(python -m pytest --version)"
echo ""

case "$SUITE" in
  unit)
    echo "[RUNNING] Unit tests..."
    exec python -m pytest \
      tests/test_api_upload.py \
      tests/test_celery_tasks.py \
      -m unit --timeout=30 -v --tb=short $EXTRA_ARGS
    ;;
  api)
    echo "[RUNNING] API integration tests..."
    exec python -m pytest \
      tests/test_api_upload.py \
      --timeout=30 -v --tb=short $EXTRA_ARGS
    ;;
  celery)
    echo "[RUNNING] Celery task tests..."
    exec python -m pytest \
      tests/test_celery_tasks.py \
      -m "celery and unit" --timeout=30 -v --tb=short $EXTRA_ARGS
    ;;
  cloudinary)
    echo "[RUNNING] Cloudinary integration tests..."
    exec python -m pytest \
      tests/test_cloudinary_integration.py \
      -m cloudinary --timeout=60 -v -s $EXTRA_ARGS
    ;;
  e2e)
    echo "[RUNNING] End-to-end photographer flow..."
    exec python -m pytest \
      tests/test_e2e_photographer_flow.py \
      -m e2e --timeout=180 -v -s $EXTRA_ARGS
    ;;
  coverage)
    echo "[RUNNING] Coverage analysis..."
    exec python -m pytest \
      tests/ \
      --cov=. \
      --cov-report=term-missing \
      --cov-report=html:htmlcov \
      --cov-fail-under=80 \
      --timeout=60 -v --tb=short $EXTRA_ARGS
    ;;
  all)
    echo "[RUNNING] Full system suite..."
    exec python -m pytest \
      tests/ \
      --timeout=120 -v --tb=short $EXTRA_ARGS
    ;;
  *)
    echo "ERROR: Unknown suite '$SUITE'"
    echo "Usage: docker compose run --rm test [unit|api|celery|cloudinary|e2e|coverage|all]"
    exit 1
    ;;
esac