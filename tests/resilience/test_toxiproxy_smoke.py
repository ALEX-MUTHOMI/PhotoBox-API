import os
import socket


def _can_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def test_postgres_proxy_socket_is_bounded():
    host = os.environ.get("DB_HOST", "postgres_proxy")
    port = int(os.environ.get("DB_PORT", "5432"))

    assert _can_connect(host, port)


def test_redis_proxy_socket_is_bounded():
    redis_url = os.environ.get("REDIS_URL", "redis://redis_proxy:6379/0")
    host = redis_url.split("://", 1)[-1].split(":", 1)[0]
    port = int(redis_url.rsplit(":", 1)[-1].split("/", 1)[0])

    assert _can_connect(host, port)
