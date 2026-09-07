"""Deploy compose contracts for Redis eviction, archive worker, and migrate boot."""


def _service_command(service: dict) -> str:
    command = service.get("command")
    if isinstance(command, list):
        return " ".join(str(part) for part in command)
    return str(command or "")


def test_deploy_redis_uses_noeviction(deploy_compose):
    redis = deploy_compose["services"]["redis"]
    command = _service_command(redis)
    assert "noeviction" in command
    assert "allkeys-lru" not in command
    assert "appendonly yes" in command


def test_deploy_celery_archive_consumes_archive_zip(deploy_compose):
    archive = deploy_compose["services"]["celery-archive"]
    command = _service_command(archive)
    assert "archive-zip" in command
    assert "prefetch-multiplier=1" in command or "--prefetch-multiplier=1" in command
    assert archive.get("stop_grace_period") == "10m"
    image_worker = _service_command(deploy_compose["services"]["celery"])
    assert "archive-zip" not in image_worker


def test_deploy_migrate_job_gates_app_and_workers(deploy_compose):
    migrate = deploy_compose["services"]["migrate"]
    command = _service_command(migrate)
    assert "wait_for_db" in command
    assert "migrate --noinput" in command
    assert migrate.get("restart") == "no"
    env = {
        item.split("=", 1)[0]: item.split("=", 1)[1] if "=" in item else ""
        for item in (migrate.get("environment") or [])
    }
    assert env.get("RUN_MIGRATIONS") == "1"
    assert "IP_HASH_SALT" in env

    for name in ("app", "celery", "celery-archive", "celery-beat"):
        depends = deploy_compose["services"][name].get("depends_on") or {}
        assert "migrate" in depends
        condition = depends["migrate"]
        if isinstance(condition, dict):
            assert condition.get("condition") == "service_completed_successfully"


def test_deploy_app_healthcheck_uses_liveness(deploy_compose):
    health = deploy_compose["services"]["app"]["healthcheck"]["test"]
    joined = " ".join(str(part) for part in health)
    assert "/health/" in joined
