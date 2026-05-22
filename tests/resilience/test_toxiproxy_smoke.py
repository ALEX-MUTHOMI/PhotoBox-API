import json
import os
import socket
import urllib.error
import urllib.request

import psycopg2
import pytest
import redis


TOXIPROXY_API = os.environ.get("TOXIPROXY_API", "http://toxiproxy:8474")


def _can_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _toxiproxy_request(method: str, path: str, payload: dict | None = None) -> None:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        f"{TOXIPROXY_API}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=5):
            return
    except urllib.error.HTTPError as exc:
        if method == "DELETE" and exc.code == 404:
            return
        raise


def _add_toxic(proxy: str, name: str, toxic_type: str, attributes: dict, stream: str = "downstream") -> None:
    _toxiproxy_request(
        "POST",
        f"/proxies/{proxy}/toxics",
        {
            "name": name,
            "type": toxic_type,
            "stream": stream,
            "toxicity": 1.0,
            "attributes": attributes,
        },
    )


def _delete_toxic(proxy: str, name: str) -> None:
    _toxiproxy_request("DELETE", f"/proxies/{proxy}/toxics/{name}")


def _postgres_smoke(timeout: int = 2) -> int:
    with psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "devdb"),
        user=os.environ.get("DB_USER", "devuser"),
        password=os.environ.get("DB_PASS", "changeme"),
        host=os.environ.get("DB_HOST", "postgres_proxy"),
        port=int(os.environ.get("DB_PORT", "5432")),
        connect_timeout=timeout,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone()[0]


def _redis_smoke(timeout: float = 2.0) -> bool:
    client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://redis_proxy:6379/0"),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    return bool(client.ping())


def test_postgres_proxy_socket_is_bounded():
    host = os.environ.get("DB_HOST", "postgres_proxy")
    port = int(os.environ.get("DB_PORT", "5432"))

    assert _can_connect(host, port)


def test_redis_proxy_socket_is_bounded():
    redis_url = os.environ.get("REDIS_URL", "redis://redis_proxy:6379/0")
    host = redis_url.split("://", 1)[-1].split(":", 1)[0]
    port = int(redis_url.rsplit(":", 1)[-1].split("/", 1)[0])

    assert _can_connect(host, port)


def test_postgres_latency_cut_and_recovery_are_bounded():
    proxy = "postgres_proxy"
    latency = "postgres_latency_smoke"
    cut = "postgres_cut_smoke"
    _delete_toxic(proxy, latency)
    _delete_toxic(proxy, cut)
    try:
        assert _postgres_smoke() == 1

        _add_toxic(proxy, latency, "latency", {"latency": 100, "jitter": 0})
        assert _postgres_smoke(timeout=3) == 1
        _delete_toxic(proxy, latency)

        _add_toxic(proxy, cut, "timeout", {"timeout": 0})
        with pytest.raises(Exception):
            _postgres_smoke(timeout=1)
        _delete_toxic(proxy, cut)

        assert _postgres_smoke(timeout=3) == 1
    finally:
        _delete_toxic(proxy, latency)
        _delete_toxic(proxy, cut)


def test_redis_latency_cut_and_recovery_are_bounded():
    proxy = "redis_proxy"
    latency = "redis_latency_smoke"
    cut = "redis_cut_smoke"
    _delete_toxic(proxy, latency)
    _delete_toxic(proxy, cut)
    try:
        assert _redis_smoke()

        _add_toxic(proxy, latency, "latency", {"latency": 100, "jitter": 0})
        assert _redis_smoke(timeout=3)
        _delete_toxic(proxy, latency)

        _add_toxic(proxy, cut, "timeout", {"timeout": 0})
        with pytest.raises(Exception):
            _redis_smoke(timeout=1)
        _delete_toxic(proxy, cut)

        assert _redis_smoke(timeout=3)
    finally:
        _delete_toxic(proxy, latency)
        _delete_toxic(proxy, cut)
