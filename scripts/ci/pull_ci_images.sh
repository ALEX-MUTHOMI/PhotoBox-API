#!/usr/bin/env bash
# Pull the run-scoped GHCR image and retag for Compose (photobox-api-app:dev).
set -euo pipefail

cd "$(dirname "$0")/../.."

OWNER="$(echo "${GITHUB_REPOSITORY_OWNER:-local}" | tr '[:upper:]' '[:lower:]')"
REPO="$(echo "${GITHUB_REPOSITORY##*/}" | tr '[:upper:]' '[:lower:]')"
SHA="${GITHUB_SHA:-$(git rev-parse HEAD)}"

REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
CI_TAG="${CI_IMAGE_TAG:-${REGISTRY}/${OWNER}/${REPO}/photobox-api-app:ci-${SHA}}"
LOCAL_TAG="photobox-api-app:dev"

echo "Pulling ${CI_TAG}"
if ! docker pull "${CI_TAG}"; then
  cat >&2 <<EOF
ERROR: CI image not found: ${CI_TAG}
Fortress/promo jobs expect Secure PhotoBox CI to have pushed
  ${REGISTRY}/${OWNER}/${REPO}/photobox-api-app:ci-<full-sha>
Use the full github.sha from a green build-images job (abbreviated SHAs
are expanded by ci-fortress.yml when possible).
EOF
  exit 1
fi
docker tag "${CI_TAG}" "${LOCAL_TAG}"
echo "Retagged ${CI_TAG} -> ${LOCAL_TAG}"
