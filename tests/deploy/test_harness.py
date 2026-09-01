def test_every_deploy_artifact_under_test_is_readable(repo_root, app_root, scripts_root):
    expected = [
        repo_root / "docker-compose.yml",
        repo_root / "docker-compose-deploy.yml",
        repo_root / "proxy" / "default.conf.tpl",
        repo_root / "proxy" / "Dockerfile",
        repo_root / "proxy" / "run.sh",
        repo_root / ".github" / "workflows" / "ci.yml",
        scripts_root / "run-tests.sh",
        scripts_root / "run.sh",
        app_root / "pytest.ini",
        app_root / "app" / "settings.py",
    ]

    unreadable = [str(path) for path in expected if not path.is_file()]

    assert unreadable == [], (
        "deploy artifacts not readable from inside the container: "
        f"{unreadable}. Check the read-only mounts on the app/test services "
        "in docker-compose.yml."
    )


def test_yaml_parser_is_available_in_the_test_image():
    import yaml

    assert yaml.safe_load("a: 1") == {"a": 1}
