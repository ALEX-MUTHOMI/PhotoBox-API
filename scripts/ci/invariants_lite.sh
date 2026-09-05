#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=scripts/ci/_compose.sh
source "$(dirname "$0")/_compose.sh"

compose run --rm test python -m pytest \
  gallery/tests/invariants/ \
  -m dsa \
  --timeout=120 -v --tb=short "$@"
