#!/usr/bin/env sh
# Build all three images tagged with the current commit SHA.
#   scripts/build-images.sh                # refuses to build from uncommitted changes
#   ALLOW_DIRTY=1 scripts/build-images.sh
set -eu
cd "$(dirname "$0")/.."

sha=$(git rev-parse --short HEAD)
if [ -n "$(git status --porcelain)" ]; then
  if [ "${ALLOW_DIRTY:-0}" != "1" ]; then
    echo "Working tree has uncommitted changes; an image tagged $sha would not match that commit." >&2
    exit 1
  fi
  sha="$sha-dirty"
fi

GIT_SHA=$sha docker compose build
echo "Built novaops/{mcp,api,renewal-worker}:$sha"
