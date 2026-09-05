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
docker pull "${CI_TAG}"
docker tag "${CI_TAG}" "${LOCAL_TAG}"
echo "Retagged ${CI_TAG} -> ${LOCAL_TAG}"
