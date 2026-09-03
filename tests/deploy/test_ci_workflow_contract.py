from pathlib import Path


def test_single_ci_workflow_is_canonical(repo_root):
    workflows = repo_root / ".github" / "workflows"
    yml_files = sorted(path.name for path in workflows.glob("*.yml"))
    assert "ci.yml" in yml_files
    assert "checks.yml" not in yml_files


def test_ci_workflow_includes_required_gates(repo_root, scripts_root):
    ci_text = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    run_tests = (scripts_root / "run-tests.sh").read_text(encoding="utf-8")
    django_smoke = (scripts_root / "ci" / "django_smoke.sh").read_text(encoding="utf-8")
    security = (scripts_root / "ci" / "security.sh").read_text(encoding="utf-8")

    assert "name: validate" in ci_text
    assert "name: security" in ci_text
    assert "name: unit" in ci_text
    assert "name: coverage" in ci_text
    assert "scripts/ci/coverage.sh" in ci_text
    assert "--cov-fail-under=70" in run_tests
    assert "needs: [unit, celery, coverage]" in ci_text
    assert "::add-mask::" in ci_text
    assert "makemigrations --check" in django_smoke
    assert "bandit" in security
    assert "pip-audit" in security
    assert "checkout/tests/" in run_tests
    assert "ingestion/tests/" in run_tests
