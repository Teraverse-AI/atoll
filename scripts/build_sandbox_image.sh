#!/bin/sh
# Build the Atoll code-execution sandbox image with Docker.
# Usage: scripts/build_sandbox_image.sh [tag]   (default tag: atoll-sandbox:latest)
set -eu

TAG="${1:-atoll-sandbox:latest}"
DIR="$(dirname "$0")/../environment/sandbox"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found on PATH (install Docker Desktop)" >&2
  exit 1
fi

echo "Building $TAG with docker ..."
docker build -t "$TAG" "$DIR"
echo "Done. Atoll will use it automatically (override with ATOLL_SANDBOX_IMAGE)."
