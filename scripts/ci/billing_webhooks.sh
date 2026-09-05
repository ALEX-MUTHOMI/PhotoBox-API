#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=scripts/ci/_compose.sh
source "$(dirname "$0")/_compose.sh"

compose run --rm test python -m pytest \
  billing/tests/test_daraja_callback.py \
  billing/tests/test_security.py \
  billing/tests/test_transaction_lifecycle.py \
  --timeout=60 -v --tb=short "$@"
