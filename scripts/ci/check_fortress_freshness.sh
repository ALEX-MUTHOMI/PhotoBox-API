#!/usr/bin/env bash
# Fail if the last green fortress-gate is older than FORTRESS_MAX_AGE_DAYS.
set -euo pipefail

REPO="${1:?usage: check_fortress_freshness.sh OWNER/REPO}"
MAX_DAYS="${FORTRESS_MAX_AGE_DAYS:-7}"

echo "Checking fortress freshness for ${REPO} (max ${MAX_DAYS} days)"

# Prefer the latest successful fortress-gate run.
run_json="$(gh run list --repo "${REPO}" --workflow "ci-fortress.yml" --status success --limit 5 --json databaseId,conclusion,updatedAt,name 2>/dev/null || true)"
if [ -z "$run_json" ] || [ "$run_json" = "[]" ]; then
  echo "::error::No successful fortress workflow runs found. Run ci-fortress.yml before staging promote."
  exit 1
fi

python - <<'PY' "$run_json" "$MAX_DAYS"
import json, sys
from datetime import datetime, timezone, timedelta

runs = json.loads(sys.argv[1])
max_days = int(sys.argv[2])
cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
latest = None
for r in runs:
    if r.get("conclusion") != "success":
        continue
    ts = datetime.fromisoformat(r["updatedAt"].replace("Z", "+00:00"))
    if latest is None or ts > latest:
        latest = ts
if latest is None:
    print("::error::No successful fortress runs in recent history.")
    sys.exit(1)
print(f"Latest fortress success: {latest.isoformat()}")
if latest < cutoff:
    print(f"::error::Fortress success older than {max_days} days ({latest.isoformat()}).")
    sys.exit(1)
print("Fortress freshness OK.")
PY
