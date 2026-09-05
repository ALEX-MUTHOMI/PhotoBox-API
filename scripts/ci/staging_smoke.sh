#!/usr/bin/env bash
# Staging smoke: health + anonymous share_code probe must stay JSON 404.
set -euo pipefail

BASE_URL="${1:?usage: staging_smoke.sh https://staging.example}"
BASE_URL="${BASE_URL%/}"

echo "Staging smoke against ${BASE_URL}"

health="$(curl -fsS --max-time 30 "${BASE_URL}/api/health-check/")"
echo "health: ${health}"
echo "$health" | grep -q '"healthy"' || {
  echo "health payload missing healthy flag" >&2
  exit 1
}
echo "$health" | grep -qi '"debug"' && {
  echo "health must not leak debug" >&2
  exit 1
}

code="$(curl -sS -o /tmp/pb-g1.json -w '%{http_code}' --max-time 30 \
  -H 'Accept: application/json' \
  "${BASE_URL}/api/galleries/g/1/")"
echo "GET /api/galleries/g/1/ -> ${code}"
test "$code" = "404" || {
  echo "expected 404 for sequential share_code probe" >&2
  cat /tmp/pb-g1.json >&2
  exit 1
}
grep -qi '<html' /tmp/pb-g1.json && {
  echo "404 body must not be HTML" >&2
  exit 1
}

ctype="$(curl -sS -o /dev/null -w '%{content_type}' --max-time 30 \
  -H 'Accept: application/json' \
  "${BASE_URL}/api/galleries/g/1/")"
echo "content-type: ${ctype}"
echo "$ctype" | grep -qi 'json' || {
  echo "expected application/json 404" >&2
  exit 1
}

echo "Staging smoke passed."
