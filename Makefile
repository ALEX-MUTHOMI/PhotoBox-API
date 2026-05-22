.PHONY: validate lint security smoke test test-unit test-celery test-integration test-toxiproxy docker-build ci-local
.PHONY: test-api test-cloudinary test-e2e test-coverage legacy-test-all

validate:
	bash scripts/ci/validate.sh

lint:
	bash scripts/ci/lint.sh

security:
	bash scripts/ci/security.sh

smoke:
	bash scripts/ci/django_smoke.sh

test: test-unit

test-unit:
	bash scripts/ci/unit.sh

test-celery:
	bash scripts/ci/celery.sh

test-integration:
	bash scripts/ci/integration.sh

test-toxiproxy:
	bash scripts/ci/toxiproxy_smoke.sh

docker-build:
	bash scripts/ci/docker_build.sh

ci-local: validate lint security smoke test-unit test-celery test-integration test-toxiproxy docker-build

test-api:
	docker compose run --rm test api

test-cloudinary:
	docker compose run --rm test cloudinary

test-e2e:
	docker compose run --rm test e2e

test-coverage:
	docker compose run --rm test coverage

legacy-test-all:
	docker compose run --rm test all
