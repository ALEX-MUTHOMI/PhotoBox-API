#!/usr/bin/env bash
# Prune GHCR :ci-* package versions older than RETENTION_DAYS (default 14).
# Retains development / staging / main / v* tags.
set -euo pipefail

RETENTION_DAYS="${RETENTION_DAYS:-14}"
OWNER="$(echo "${GITHUB_REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')"
REPO="$(echo "${GITHUB_REPOSITORY##*/}" | tr '[:upper:]' '[:lower:]')"
PACKAGE="photobox-api-app"
# Container packages use org/user API with URL-encoded slash for nested names.
PACKAGE_NAME="${REPO}%2F${PACKAGE}"

CUTOFF_EPOCH="$(date -u -d "${RETENTION_DAYS} days ago" +%s 2>/dev/null || date -u -v-"${RETENTION_DAYS}"d +%s)"
export CUTOFF_EPOCH

echo "Pruning ${OWNER}/${REPO}/${PACKAGE} older than ${RETENTION_DAYS}d (before ${CUTOFF_EPOCH})"

page=1
deleted=0
while true; do
  resp="$(gh api \
    "users/${OWNER}/packages/container/${PACKAGE_NAME}/versions?per_page=100&page=${page}" \
    2>/dev/null || true)"
  if [ -z "$resp" ] || [ "$resp" = "[]" ]; then
    # Try org endpoint if user endpoint fails.
    resp="$(gh api \
      "orgs/${OWNER}/packages/container/${PACKAGE_NAME}/versions?per_page=100&page=${page}" \
      2>/dev/null || true)"
  fi
  if [ -z "$resp" ] || [ "$resp" = "[]" ]; then
    break
  fi

  echo "$resp" | python -c "
import json, os, sys
from datetime import datetime, timezone

cutoff = int(os.environ['CUTOFF_EPOCH'])
data = json.load(sys.stdin)
keep_prefixes = ('development', 'staging', 'main', 'v')
for ver in data:
    tags = (ver.get('metadata') or {}).get('container', {}).get('tags') or []
    updated = ver.get('updated_at') or ver.get('created_at') or ''
    try:
        ts = int(datetime.fromisoformat(updated.replace('Z', '+00:00')).timestamp())
    except Exception:
        continue
    if ts >= cutoff:
        continue
    # Keep release / branch tags; prune ci-* and untagged only.
    if any(t == 'development' or t == 'staging' or t == 'main' or t.startswith('v') for t in tags):
        continue
    if tags and not any(t.startswith('ci-') for t in tags):
        continue
    print(ver['id'])
" | while read -r vid; do
    [ -z "$vid" ] && continue
    echo "Deleting package version ${vid}"
    gh api -X DELETE "users/${OWNER}/packages/container/${PACKAGE_NAME}/versions/${vid}" \
      || gh api -X DELETE "orgs/${OWNER}/packages/container/${PACKAGE_NAME}/versions/${vid}" \
      || true
    deleted=$((deleted + 1))
  done

  count="$(echo "$resp" | python -c 'import json,sys; print(len(json.load(sys.stdin)))')"
  if [ "$count" -lt 100 ]; then
    break
  fi
  page=$((page + 1))
done

echo "Cleanup finished (attempted deletes this run)."
