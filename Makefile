# ─────────────────────────────────────────────────────────────────────────────
# Makefile — Enterprise test runner commands
# ─────────────────────────────────────────────────────────────────────────────
#
# Usage:
#   make test-unit          → fast, no external services (CI/pre-commit)
#   make test-celery        → Celery tasks in eager mode
#   make test-cloudinary    → real Cloudinary API (needs env vars)
#   make test-cloudflare    → real Cloudflare Worker (needs staging URL)
#   make test-e2e           → full photographer flow against staging
#   make test-all           → everything
#   make test-coverage      → unit + integration with coverage report
#
# Required environment variables for integration/E2E tests:
#   CLOUDINARY_CLOUD_NAME
#   CLOUDINARY_API_KEY
#   CLOUDINARY_API_SECRET
#   CLOUDFLARE_WORKER_URL
#   CF_TEST_TOKEN
#   CELERY_BROKER_URL
#   STAGING_BASE_URL
#   E2E_PHOTOGRAPHER_USERNAME
#   E2E_PHOTOGRAPHER_PASSWORD
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: test-unit test-celery test-cloudinary test-cloudflare test-e2e test-all test-coverage ci-validate ci-lint ci-security ci-django-smoke ci-unit ci-celery ci-toxiproxy

# Unit tests only — no external services, no broker — safe for pre-commit hooks
test-unit:
	pytest tests/ -m "unit" \
		--timeout=10 \
		-v \
		--tb=short

# Celery tasks in eager mode (synchronous, no broker needed)
test-celery-unit:
	pytest tests/test_celery_tasks.py -m "celery and unit" \
		--timeout=30 \
		-v

# Celery integration — requires running Redis
test-celery-integration:
	pytest tests/test_celery_tasks.py -m "celery and integration" \
		--timeout=120 \
		-v

# Cloudinary — hits the real API, creates and deletes real resources
test-cloudinary:
	pytest tests/test_cloudinary_integration.py -m cloudinary \
		--timeout=60 \
		-v \
		-s

# Cloudflare — hits the real staging Worker
test-cloudflare:
	pytest tests/test_cloudflare_integration.py -m cloudflare \
		--timeout=60 \
		-v \
		-s

# Full E2E — the real photographer flow against staging
test-e2e:
	pytest tests/test_e2e_photographer_flow.py -m e2e \
		--timeout=180 \
		-v \
		-s

# API layer tests (unit + schema + auth — mocks external calls)
test-api:
	pytest tests/test_api_upload.py \
		--timeout=30 \
		-v

# Everything
test-all:
	pytest tests/ \
		--timeout=180 \
		-v

# Coverage report (unit + API tests — not integration to avoid flakiness in CI)
test-coverage:
	pytest tests/test_api_upload.py tests/test_celery_tasks.py \
		-m "unit or (celery and unit)" \
		--cov=photos \
		--cov-report=term-missing \
		--cov-report=html:htmlcov \
		--cov-fail-under=80 \
		--timeout=30

# Parallel execution (faster for large test suites)
test-parallel:
	pytest tests/ -m "unit" \
		-n auto \
		--timeout=30

# Dockerized CI-aligned verification targets
ci-validate:
	docker compose config -q

ci-lint:
	docker compose run --rm test flake8

ci-security:
	docker compose run --rm test security -x

ci-django-smoke:
	docker compose run --rm -v "$$(pwd)/scripts:/scripts" --entrypoint sh test /scripts/ci/django_smoke.sh

ci-unit:
	docker compose run --rm test unit

ci-celery:
	docker compose run --rm test celery

ci-toxiproxy:
	docker compose -f docker-compose.yml -f docker-compose.toxiproxy.yml --profile toxiproxy run --rm -v "$$(pwd)/scripts:/scripts" toxiproxy-test
