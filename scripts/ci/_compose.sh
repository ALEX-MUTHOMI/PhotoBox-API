#!/usr/bin/env bash
# Shared compose helper for CI scripts.
# shellcheck shell=bash

compose() {
  local env_file="${COMPOSE_SAFE_ENV_FILE:-.env.example}"
  if [ ! -f "$env_file" ]; then
    env_file="/dev/null"
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    docker-compose --env-file "$env_file" "$@"
  else
    docker compose --env-file "$env_file" "$@"
  fi
}
