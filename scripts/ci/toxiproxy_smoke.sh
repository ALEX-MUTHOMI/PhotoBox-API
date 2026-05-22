#!/bin/sh

set -eu

TOXIPROXY_URL="${TOXIPROXY_URL:-http://toxiproxy:8474}"

echo "[toxiproxy] waiting for admin API at ${TOXIPROXY_URL}"
python - <<'PY'
import json
import os
import time
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
deadline = time.time() + 30
last_error = None

while time.time() < deadline:
    try:
        with urllib.request.urlopen(f"{base}/version", timeout=3) as response:
            if response.status == 200:
                print("[toxiproxy] admin API ready")
                raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - script path
        last_error = exc
        time.sleep(1)

raise SystemExit(f"[toxiproxy] admin API did not become ready: {last_error}")
PY

python - <<'PY'
import json
import os
import urllib.error
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")

def request(method, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            body = response.read().decode("utf-8")
            return response.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")

for proxy_name in ("postgres_proxy", "redis_proxy"):
    request("DELETE", f"/proxies/{proxy_name}")

status, body = request(
    "POST",
    "/proxies",
    {
        "name": "postgres_proxy",
        "listen": "0.0.0.0:5432",
        "upstream": "db:5432",
    },
)
assert status in (200, 201), (status, body)

status, body = request(
    "POST",
    "/proxies",
    {
        "name": "redis_proxy",
        "listen": "0.0.0.0:6379",
        "upstream": "redis:6379",
    },
)
assert status in (200, 201), (status, body)
print("[toxiproxy] proxies created")
PY

echo "[toxiproxy] baseline db query"
timeout 20s python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('SELECT 1'); print(c.fetchone()[0])"

echo "[toxiproxy] baseline redis cache roundtrip"
timeout 20s python manage.py shell -c "from django.core.cache import cache; cache.set('toxiproxy-smoke', 'ok', 30); assert cache.get('toxiproxy-smoke') == 'ok'; print('redis-ok')"

echo "[toxiproxy] inject postgres latency"
python - <<'PY'
import json
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
req = urllib.request.Request(
    f"{base}/proxies/postgres_proxy/toxics",
    data=json.dumps(
        {
            "name": "postgres_latency",
            "type": "latency",
            "stream": "downstream",
            "attributes": {"latency": 1500, "jitter": 0},
        }
    ).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=5):
    print("[toxiproxy] postgres latency toxic created")
PY
timeout 20s python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('SELECT 1'); print(c.fetchone()[0])"

echo "[toxiproxy] cut postgres"
python - <<'PY'
import json
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
req = urllib.request.Request(
    f"{base}/proxies/postgres_proxy/toxics",
    data=json.dumps(
        {
            "name": "postgres_cut",
            "type": "timeout",
            "stream": "downstream",
            "attributes": {"timeout": 0},
        }
    ).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=5):
    print("[toxiproxy] postgres cut toxic created")
PY
if timeout 20s python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('SELECT 1'); print(c.fetchone()[0])"; then
  echo "[toxiproxy] expected postgres cut to fail"
  exit 1
fi

echo "[toxiproxy] reset postgres proxy"
python - <<'PY'
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
for toxic in ("postgres_latency", "postgres_cut"):
    req = urllib.request.Request(
        f"{base}/proxies/postgres_proxy/toxics/{toxic}",
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
print("[toxiproxy] postgres toxics cleared")
PY
timeout 20s python manage.py shell -c "from django.db import connection; c=connection.cursor(); c.execute('SELECT 1'); print(c.fetchone()[0])"

echo "[toxiproxy] inject redis latency"
python - <<'PY'
import json
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
req = urllib.request.Request(
    f"{base}/proxies/redis_proxy/toxics",
    data=json.dumps(
        {
            "name": "redis_latency",
            "type": "latency",
            "stream": "downstream",
            "attributes": {"latency": 1500, "jitter": 0},
        }
    ).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=5):
    print("[toxiproxy] redis latency toxic created")
PY
timeout 20s python manage.py shell -c "from django.core.cache import cache; cache.set('toxiproxy-smoke-latency', 'ok', 30); assert cache.get('toxiproxy-smoke-latency') == 'ok'; print('redis-ok')"

echo "[toxiproxy] cut redis"
python - <<'PY'
import json
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
req = urllib.request.Request(
    f"{base}/proxies/redis_proxy/toxics",
    data=json.dumps(
        {
            "name": "redis_cut",
            "type": "timeout",
            "stream": "downstream",
            "attributes": {"timeout": 0},
        }
    ).encode("utf-8"),
    method="POST",
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(req, timeout=5):
    print("[toxiproxy] redis cut toxic created")
PY
if timeout 20s python manage.py shell -c "from django.core.cache import cache; cache.set('toxiproxy-smoke-cut', 'ok', 30); print(cache.get('toxiproxy-smoke-cut'))"; then
  echo "[toxiproxy] expected redis cut to fail"
  exit 1
fi

echo "[toxiproxy] reset redis proxy"
python - <<'PY'
import os
import urllib.request

base = os.environ["TOXIPROXY_URL"].rstrip("/")
for toxic in ("redis_latency", "redis_cut"):
    req = urllib.request.Request(
        f"{base}/proxies/redis_proxy/toxics/{toxic}",
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
print("[toxiproxy] redis toxics cleared")
PY
timeout 20s python manage.py shell -c "from django.core.cache import cache; cache.set('toxiproxy-smoke-reset', 'ok', 30); assert cache.get('toxiproxy-smoke-reset') == 'ok'; print('redis-ok')"

echo "[toxiproxy] completed"
