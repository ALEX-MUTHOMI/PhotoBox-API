"""Fixtures for deploy-configuration contract tests."""
import os
import re
from pathlib import Path

import pytest

DEFAULT_REPO_ROOT = "/repo-root"
DEFAULT_APP_ROOT = "/app"
DEFAULT_SCRIPTS_ROOT = "/scripts"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(os.environ.get("PHOTOBOX_REPO_ROOT", DEFAULT_REPO_ROOT))


@pytest.fixture(scope="session")
def app_root() -> Path:
    return Path(os.environ.get("PHOTOBOX_APP_ROOT", DEFAULT_APP_ROOT))


@pytest.fixture(scope="session")
def scripts_root() -> Path:
    return Path(os.environ.get("PHOTOBOX_SCRIPTS_ROOT", DEFAULT_SCRIPTS_ROOT))


def load_compose(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def dev_compose(repo_root) -> dict:
    return load_compose(repo_root / "docker-compose.yml")


@pytest.fixture(scope="session")
def deploy_compose(repo_root) -> dict:
    return load_compose(repo_root / "docker-compose-deploy.yml")


def service_env(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(key): ("" if value is None else str(value)) for key, value in env.items()}
    pairs = {}
    for item in env:
        key, _, value = str(item).partition("=")
        pairs[key.strip()] = value.strip()
    return pairs
