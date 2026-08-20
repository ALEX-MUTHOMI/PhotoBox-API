import json
import multiprocessing
import os
import socket
import time
import urllib.error
import urllib.request
import uuid

from celery import Celery
import psycopg2
import pytest
import redis


TOXIPROXY_API = os.environ.get("TOXIPROXY_API", "http://toxiproxy:8474")
PROXIES = ("postgres_proxy", "redis_proxy")


def _can_connect(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _toxiproxy_request(method: str, path: str, payload: dict | None = None) -> dict | None:
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
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read()
            if not body:
                return None
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if method == "DELETE" and exc.code == 404:
            return None
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


def _set_proxy_enabled(proxy: str, enabled: bool) -> None:
    _toxiproxy_request("PATCH", f"/proxies/{proxy}", {"enabled": enabled})


def _list_toxics(proxy: str) -> list[str]:
    data = _toxiproxy_request("GET", f"/proxies/{proxy}") or {}
    toxics = data.get("toxics", {})
    if isinstance(toxics, dict):
        return sorted(toxics)
    return sorted(toxic["name"] for toxic in toxics)


def _clear_toxics(proxy: str) -> None:
    deadline = time.monotonic() + 3
    while True:
        toxics = _list_toxics(proxy)
        if not toxics:
            return
        for toxic in toxics:
            _delete_toxic(proxy, toxic)
        if time.monotonic() > deadline:
            return
        time.sleep(0.1)


@pytest.fixture(autouse=True)
def toxiproxy_hygiene():
    for proxy in PROXIES:
        _set_proxy_enabled(proxy, True)
        _clear_toxics(proxy)
        assert _list_toxics(proxy) == []
    try:
        yield
    finally:
        for proxy in PROXIES:
            _set_proxy_enabled(proxy, True)
            _clear_toxics(proxy)
            assert _list_toxics(proxy) == []


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


def _postgres_connection(timeout: int = 2):
    return psycopg2.connect(
        dbname=os.environ.get("DB_NAME", "devdb"),
        user=os.environ.get("DB_USER", "devuser"),
        password=os.environ.get("DB_PASS", "changeme"),
        host=os.environ.get("DB_HOST", "postgres_proxy"),
        port=int(os.environ.get("DB_PORT", "5432")),
        connect_timeout=timeout,
    )


def _ensure_probe_table() -> None:
    with _postgres_connection() as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS toxiproxy_resilience_probe (
                    id text PRIMARY KEY,
                    marker text NOT NULL
                )
                """
            )


def _probe_exists(probe_id: str) -> bool:
    with _postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM toxiproxy_resilience_probe WHERE id = %s)",
                (probe_id,),
            )
            return bool(cursor.fetchone()[0])


def _delete_probe(probe_id: str) -> None:
    with _postgres_connection() as connection:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM toxiproxy_resilience_probe WHERE id = %s", (probe_id,))


def _transaction_worker(probe_id: str, ready: multiprocessing.Event, proceed: multiprocessing.Event, result):
    connection = None
    try:
        connection = _postgres_connection()
        connection.autocommit = False
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO toxiproxy_resilience_probe (id, marker) VALUES (%s, %s)",
                (probe_id, "pending"),
            )
        ready.set()
        if not proceed.wait(timeout=5):
            result.put(("error", "parent did not release commit"))
            return
        connection.commit()
        result.put(("committed", None))
    except Exception as exc:
        result.put(("error", exc.__class__.__name__))
    finally:
        if connection is not None:
            connection.close()


def _redis_smoke(timeout: float = 2.0) -> bool:
    return bool(_redis_client(timeout=timeout).ping())


def _redis_client(timeout: float = 2.0) -> redis.Redis:
    client = redis.Redis.from_url(
        os.environ.get("REDIS_URL", "redis://redis_proxy:6379/0"),
        socket_connect_timeout=timeout,
        socket_timeout=timeout,
    )
    return client


def _assert_bounded(started_at: float, budget_seconds: float = 7.0) -> None:
    assert time.monotonic() - started_at < budget_seconds


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
    try:
        assert _postgres_smoke() == 1

        _add_toxic(proxy, latency, "latency", {"latency": 100, "jitter": 0})
        started_at = time.monotonic()
        assert _postgres_smoke(timeout=3) == 1
        _assert_bounded(started_at)
        _delete_toxic(proxy, latency)

        _add_toxic(proxy, cut, "timeout", {"timeout": 0})
        started_at = time.monotonic()
        with pytest.raises(Exception):
            _postgres_smoke(timeout=1)
        _assert_bounded(started_at)
        _delete_toxic(proxy, cut)

        assert _postgres_smoke(timeout=3) == 1
    finally:
        _delete_toxic(proxy, latency)
        _delete_toxic(proxy, cut)


def test_postgres_cut_during_transaction_does_not_partially_commit():
    proxy = "postgres_proxy"
    cut = "postgres_transaction_cut"
    probe_id = f"toxiproxy-{uuid.uuid4()}"
    _ensure_probe_table()
    _delete_probe(probe_id)

    ready = multiprocessing.Event()
    proceed = multiprocessing.Event()
    result = multiprocessing.Queue()
    process = multiprocessing.Process(target=_transaction_worker, args=(probe_id, ready, proceed, result))
    process.start()
    try:
        assert ready.wait(timeout=5)
        _add_toxic(proxy, cut, "timeout", {"timeout": 0}, stream="upstream")
        started_at = time.monotonic()
        proceed.set()
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
            outcome = ("timeout", None)
        else:
            outcome = result.get(timeout=1)
        _assert_bounded(started_at)
        assert outcome[0] in {"error", "timeout"}
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        _delete_toxic(proxy, cut)

    assert _postgres_smoke(timeout=3) == 1
    assert not _probe_exists(probe_id)


def test_redis_latency_cut_and_recovery_are_bounded():
    proxy = "redis_proxy"
    latency = "redis_latency_smoke"
    cut = "redis_cut_smoke"
    try:
        assert _redis_smoke()

        _add_toxic(proxy, latency, "latency", {"latency": 100, "jitter": 0})
        started_at = time.monotonic()
        assert _redis_smoke(timeout=3)
        _assert_bounded(started_at)
        _delete_toxic(proxy, latency)

        _add_toxic(proxy, cut, "timeout", {"timeout": 0})
        started_at = time.monotonic()
        with pytest.raises(Exception):
            _redis_smoke(timeout=1)
        _assert_bounded(started_at)
        _delete_toxic(proxy, cut)

        assert _redis_smoke(timeout=3)
    finally:
        _delete_toxic(proxy, latency)
        _delete_toxic(proxy, cut)


def test_redis_cut_during_status_write_does_not_mark_complete():
    proxy = "redis_proxy"
    cut = "redis_status_cut"
    key = f"toxiproxy:status:{uuid.uuid4()}"
    client = _redis_client(timeout=1)
    client.delete(key)

    try:
        _add_toxic(proxy, cut, "timeout", {"timeout": 0}, stream="upstream")
        started_at = time.monotonic()
        with pytest.raises(Exception):
            client.set(key, "complete")
        _assert_bounded(started_at)
    finally:
        _delete_toxic(proxy, cut)

    recovered_client = _redis_client(timeout=3)
    assert recovered_client.get(key) is None
    assert _redis_smoke(timeout=3)


def test_celery_broker_cut_is_explicit_and_not_successful():
    proxy = "redis_proxy"
    broker_url = os.environ.get("CELERY_BROKER_URL", "redis://redis_proxy:6379/0")
    task_id = f"toxiproxy-{uuid.uuid4()}"
    celery_app = Celery("toxiproxy-resilience", broker=broker_url, backend=broker_url)
    celery_app.conf.broker_connection_timeout = 1
    celery_app.conf.broker_connection_retry = False
    celery_app.conf.broker_connection_max_retries = 0
    celery_app.conf.broker_transport_options = {
        "socket_connect_timeout": 1,
        "socket_timeout": 1,
        "retry_on_timeout": False,
    }
    celery_app.conf.result_backend_transport_options = {"socket_timeout": 1}

    published = False
    try:
        _set_proxy_enabled(proxy, False)
        started_at = time.monotonic()
        with pytest.raises(Exception):
            with celery_app.connection_for_write() as connection:
                connection.ensure_connection(max_retries=0, timeout=1)
                published = True
        _assert_bounded(started_at)
    finally:
        _set_proxy_enabled(proxy, True)

    assert _redis_smoke(timeout=3)
    assert not published
    assert celery_app.AsyncResult(task_id).status == "PENDING"
