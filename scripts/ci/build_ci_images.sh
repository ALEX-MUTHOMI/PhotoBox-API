#!/usr/bin/env bash
# Build the PhotoBox app image once and push a run-scoped GHCR tag.
set -euo pipefail

cd "$(dirname "$0")/../.."

OWNER="$(echo "${GITHUB_REPOSITORY_OWNER:-local}" | tr '[:upper:]' '[:lower:]')"
REPO="$(echo "${GITHUB_REPOSITORY##*/}" | tr '[:upper:]' '[:lower:]')"
SHA="${GITHUB_SHA:-$(git rev-parse HEAD)}"
SHORT_SHA="${SHA:0:12}"

REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
IMAGE_BASE="${REGISTRY}/${OWNER}/${REPO}/photobox-api-app"
CI_TAG="${IMAGE_BASE}:ci-${SHA}"
LOCAL_TAG="photobox-api-app:dev"

echo "Building ${CI_TAG}"
docker build \
  --build-arg DEV=true \
  --build-arg APP_UID="${APP_UID:-1000}" \
  --build-arg APP_GID="${APP_GID:-1000}" \
  -t "${LOCAL_TAG}" \
  -t "${CI_TAG}" \
  -t "${IMAGE_BASE}:ci-${SHORT_SHA}" \
  .

if [ "${PUSH_CI_IMAGE:-1}" = "1" ]; then
  docker push "${CI_TAG}"
  docker push "${IMAGE_BASE}:ci-${SHORT_SHA}"
fi

mkdir -p reports/ci
{
  echo "image_ci_tag=${CI_TAG}"
  echo "image_local_tag=${LOCAL_TAG}"
  echo "git_sha=${SHA}"
} > reports/ci/ci-image.txt

echo "CI_IMAGE_TAG=${CI_TAG}" >> "${GITHUB_OUTPUT:-/dev/null}" 2>/dev/null || true
echo "Built ${CI_TAG}"
