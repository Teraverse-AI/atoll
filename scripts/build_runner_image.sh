#!/bin/sh
# Build the Atoll runner image (loop + tools; one eval per container) with Docker.
# Usage: scripts/build_runner_image.sh [tag]   (default tag: atoll-runner:latest)
set -eu

TAG="${1:-atoll-runner:latest}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if ! command -v docker >/dev/null 2>&1; then
  echo "error: docker not found on PATH (install Docker Desktop)" >&2
  exit 1
fi

echo "Building $TAG with docker ..."
docker build -t "$TAG" -f "$ROOT/agents/Dockerfile" "$ROOT"
echo "Done. Run evals with scripts/run_eval.sh (needs atoll-sandbox:latest too)."
