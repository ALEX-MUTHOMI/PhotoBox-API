#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")/../.."
# shellcheck source=scripts/ci/_compose.sh
source "$(dirname "$0")/_compose.sh"

# Kenya gallery contracts without Daraja (billing job owns Daraja).
compose run --rm test kenya "$@"
compose run --rm test security "$@"
