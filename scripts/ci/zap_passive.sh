#!/usr/bin/env bash
# OWASP ZAP passive scan against the DAST compose app (Kenya public surface).
# Never request /api/billing/daraja/ (Safaricom STK callback is out of scope).
set -eo pipefail

cd "$(dirname "$0")/../.."
ROOT="$(pwd)"
REPORT_DIR="${ROOT}/artifacts/zap"
mkdir -p "$REPORT_DIR"
cp -f scripts/zap/automation.yaml "$REPORT_DIR/automation.yaml"
chmod -R a+rwx "$REPORT_DIR" 2>/dev/null || true

compose() {
  local env_file="${COMPOSE_SAFE_ENV_FILE:-.env.example}"
  if [ ! -f "$env_file" ]; then
    env_file="/dev/null"
  fi
  local docker_bin="docker"
  if command -v docker.exe >/dev/null 2>&1; then
    docker_bin="docker.exe"
  fi
  if "$docker_bin" compose version >/dev/null 2>&1; then
    "$docker_bin" compose --env-file "$env_file" "$@"
    return
  fi
  echo "docker compose not found" >&2
  exit 1
}

echo "================================================================"
echo "  OWASP ZAP passive scan (PhotoBox API)"
echo "  Daraja callback is excluded — ZAP must not hit STK URLs."
echo "================================================================"

compose up -d db redis
compose --profile zap up -d --no-build app-dast || compose --profile zap up -d app-dast

echo "[ZAP] Waiting for app-dast health..."
for i in $(seq 1 40); do
  if compose exec -T app-dast python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health-check/', timeout=3)" >/dev/null 2>&1; then
    echo "[ZAP] App is healthy."
    break
  fi
  if [ "$i" -eq 40 ]; then
    echo "[ZAP] App failed to become healthy." >&2
    compose logs --tail=80 app-dast || true
    exit 1
  fi
  sleep 3
done

echo "[ZAP] Running Automation Framework (passive only)..."
set +e
compose --profile zap run --rm --no-deps --user root zap \
  sh -c "chmod -R 777 /zap/wrk && su zap -c 'zap.sh -cmd -autorun /zap/wrk/automation.yaml'"
ZAP_RC=$?
set -e
echo "[ZAP] zap.sh exit code: ${ZAP_RC}"

if [ ! -f "$REPORT_DIR/zap-passive.json" ]; then
  echo "[ZAP] JSON report not found under $REPORT_DIR" >&2
  ls -la "$REPORT_DIR" || true
  exit 1
fi

python scripts/zap/gate_alerts.py "$REPORT_DIR/zap-passive.json"
GATE_RC=$?

echo "[ZAP] Reports: $REPORT_DIR/zap-passive.json , $REPORT_DIR/zap-passive.html"
exit "$GATE_RC"
