def test_promo_and_fortress_workflows_exist(repo_root):
    workflows = repo_root / ".github" / "workflows"
    names = {path.name for path in workflows.glob("*.yml")}
    assert "ci.yml" in names
    assert "ci-fortress.yml" in names
    assert "ci-images-cleanup.yml" in names
    assert "codeql.yml" in names
    assert "deploy-staging.yml" in names
    assert "checks.yml" not in names


def test_promotion_gate_evaluates_needs_results(repo_root):
    ci_text = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "name: promotion-gate" in ci_text
    assert "if: always()" in ci_text
    assert "toJSON(needs.*.result)" in ci_text
    assert "failure|cancelled" in ci_text
    assert "name: lint-security" in ci_text
    assert "name: build-images" in ci_text
    assert "name: gallery-kenya" in ci_text
    assert "name: billing-webhooks" in ci_text
    assert "name: newman-kenya" in ci_text
    assert "scope=${{ github.ref_name }}-app" in ci_text
    assert "scope=development-app" in ci_text
    # Fast-fail: lint must not block image build serialization
    assert "needs: [validate]" in ci_text
    assert "needs: [build-images, lint-security]" in ci_text


def test_fortress_never_cancels_in_progress(repo_root):
    fortress = (repo_root / ".github" / "workflows" / "ci-fortress.yml").read_text(
        encoding="utf-8"
    )
    assert "group: ci-fortress" in fortress
    assert "cancel-in-progress: false" in fortress
    assert "name: fortress-gate" in fortress
    assert "toJSON(needs.*.result)" in fortress
    assert "failure|cancelled" in fortress
    assert "zap_passive.sh" in fortress
    assert "toxiproxy_smoke.sh" in fortress


def test_security_scripts_fail_closed(scripts_root):
    security = (scripts_root / "ci" / "security.sh").read_text(encoding="utf-8")
    host = (scripts_root / "ci" / "lint_security_host.sh").read_text(encoding="utf-8")
    assert "bandit is required" in security
    assert "pip-audit is required" in security
    assert "Poetry security group must be installed before enabling" not in security
    assert "pip-audit" in host
    assert "bandit" in host


def test_newman_and_zap_forbid_daraja(repo_root, scripts_root):
    newman = (scripts_root / "ci" / "newman.sh").read_text(encoding="utf-8")
    zap = (scripts_root / "ci" / "zap_passive.sh").read_text(encoding="utf-8")
    assert "daraja" in newman.lower()
    assert "exit 1" in newman
    assert "daraja" in zap.lower()
    collection = (
        repo_root / "postman" / "kenya.postman_collection.json"
    ).read_text(encoding="utf-8")
    assert "daraja" not in collection.lower()


def test_docs_workflow_watches_photobox_docs_site(repo_root):
    docs = (repo_root / ".github" / "workflows" / "docs-site.yml").read_text(
        encoding="utf-8"
    )
    assert "photobox-docs-site/**" in docs
    assert "working-directory: photobox-docs-site" in docs
    assert '"docs-site/**"' not in docs


def test_dependabot_and_codeowners_exist(repo_root):
    dependabot = (repo_root / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    codeowners = (repo_root / ".github" / "CODEOWNERS").read_text(encoding="utf-8")
    assert "insecure-external-code-execution: deny" in dependabot
    assert 'directory: "/photobox-docs-site"' in dependabot
    assert "Django" in dependabot
    assert "/app/gallery/pin_gate.py" in codeowners
    assert "/.github/workflows/" in codeowners


def test_ci_helpers_and_coverage_gate(scripts_root, repo_root):
    run_tests = (scripts_root / "run-tests.sh").read_text(encoding="utf-8")
    assert "--cov-fail-under=70" in run_tests
    assert "kenya" in run_tests
    assert (scripts_root / "ci" / "build_ci_images.sh").is_file()
    assert (scripts_root / "ci" / "pull_ci_images.sh").is_file()
    assert (scripts_root / "ci" / "cleanup_ci_images.sh").is_file()
    assert (scripts_root / "ci" / "check_fortress_freshness.sh").is_file()
    assert (scripts_root / "ci" / "staging_smoke.sh").is_file()
    mask = (
        repo_root / ".github" / "actions" / "mask-secrets" / "action.yml"
    ).read_text(encoding="utf-8")
    assert "::add-mask::" in mask
    assert "inputs:" in mask
    assert (repo_root / ".flake8").is_file()
    host = (scripts_root / "ci" / "lint_security_host.sh").read_text(encoding="utf-8")
    assert "--config=.flake8" in host
    assert "--max-line-length=100" not in host
    assert "--only main" in host


def test_scale_readme_documents_envelopes_and_elastic(repo_root):
    scale = (repo_root / "scripts" / "scale" / "README.md").read_text(encoding="utf-8")
    assert "Little" in scale
    assert "PHOTBOX_SCALE_ENVELOPE" in scale
    assert "PgBouncer" in scale
    assert "archive-zip" in scale
    assert "cl_waiting" in scale
    assert "immutable" in scale.lower() or "digest" in scale.lower()
    assert "gallery_id" in scale  # cardinality warning
    keda = repo_root / "deploy" / "keda" / "scaledobjects.example.yaml"
    assert keda.is_file()
    keda_text = keda.read_text(encoding="utf-8")
    assert "archive-zip" in keda_text
    assert "maxReplicaCount" in keda_text
    assert "Migrate" in keda_text or "migrate" in keda_text


def test_deployment_docs_require_promotion_gate(repo_root):
    deploy = (
        repo_root
        / "photobox-docs-site"
        / "src"
        / "content"
        / "docs"
        / "operations"
        / "deployment.md"
    ).read_text(encoding="utf-8")
    assert "promotion-gate" in deploy
    assert "Daraja" in deploy or "daraja" in deploy
    assert "ci-fortress" in deploy
