#!/usr/bin/env bash
# Grade ONE run of one task in containers. Reference launcher: a pipeline grades by
# issuing the same docker commands with the same flags.
#
# Usage:
#   scripts/run_grade.sh --task <task-dir> --run <run-dir> --out <grade-dir> \
#       --judge-model <model> [--env-file <file>] [-- <atoll-grade args...>]
#
#   <task-dir>   Atoll bundle (task.toml + rubric + reference/) or delivery task
#                (workspace/prompt.md, reference/rubrics.json, reference/outputs/)
#   <run-dir>    atoll-run output (result.json + snapshots) or delivery run
#                (agent_outputs/ + cli.json); mounted read-only
#   <grade-dir>  must not exist yet; receives rendered.json, score.json, egress.log
#   --env-file   judge credentials (LITELLM_BASE_URL / LITELLM_API_KEY or provider
#                keys) — given to the judge phase only
#   -- ...       extra atoll-grade flags (e.g. --concurrency 2)
#
# Two containers from the atoll-runner image, run one after the other:
#   <id>-render  parses the agent's files and the reference files (LibreOffice
#                recalculation included) · NO network · no credentials ·
#                sees: task (ro), run (ro), its grade dir
#   <id>-judge   one LLM call per rubric item · only <id>-net (Docker --internal)
#                → <id>-egress, which admits nothing but the judge endpoint
#                ($ATOLL_EGRESS_ALLOW, else the LITELLM_BASE_URL host) · sees: its
#                grade dir only (rendered.json in, score.json out)
# Same locks as scripts/run_eval.sh: read-only root, no capabilities, non-root,
# memory/CPU/process caps, no Docker socket. Everything is removed on exit.
set -euo pipefail

RUNNER_IMAGE="${ATOLL_RUNNER_IMAGE:-atoll-runner:latest}"
NETWORK="${ATOLL_RUNNER_NETWORK:-atoll-runner-net}"

TASK="" RUN="" OUT="" MODEL="" ENV_FILE="" EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --task) TASK="$2"; shift 2 ;;
    --run) RUN="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --judge-model) MODEL="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$TASK" ] && [ -n "$RUN" ] && [ -n "$OUT" ] && [ -n "$MODEL" ] \
  || { echo "usage: $0 --task DIR --run DIR --out DIR --judge-model M [--env-file F] [-- args]" >&2; exit 2; }
[ -d "$TASK" ] || { echo "task dir not found: $TASK" >&2; exit 2; }
[ -d "$RUN" ] || { echo "run dir not found: $RUN" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "grade dir already exists: $OUT" >&2; exit 2; }

TASK="$(cd "$TASK" && pwd)"
RUN="$(cd "$RUN" && pwd)"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
ID="atoll-grade-$(date +%s)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
  if docker container inspect "$ID-egress" >/dev/null 2>&1; then
    docker logs "$ID-egress" >"$OUT/egress.log" 2>&1 || true
  fi
  docker rm -f "$ID-render" "$ID-judge" "$ID-egress" >/dev/null 2>&1 || true
  docker network rm "$ID-net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

LOCKS=(--read-only --cap-drop ALL --security-opt no-new-privileges --user 1000:1000)
ENV_ARGS=()
[ -n "$ENV_FILE" ] && ENV_ARGS=(--env-file "$ENV_FILE")
PASS_ENV=()
for v in ATOLL_EGRESS_ALLOW; do
  [ -n "${!v:-}" ] && PASS_ENV+=(-e "$v=${!v}")
done

# Phase 1: render. Untrusted, agent-written files are parsed here — no network, no
# credentials.
docker run --rm --name "$ID-render" "${LOCKS[@]}" --network none \
  --memory 2g --memory-swap 2g --cpus 2 --pids-limit 256 \
  --tmpfs /tmp:rw,size=1g,mode=1777 \
  -v "$TASK:/task:ro" -v "$RUN:/run:ro" -v "$OUT:/grade" \
  --entrypoint atoll-grade "$RUNNER_IMAGE" \
  --task /task --run /run --render-only /grade \
  --task-name "$(basename "$TASK")" --run-label "$(basename "$(dirname "$RUN")")/$(basename "$RUN")"

# Phase 2: judge, reaching only the judge endpoint through a per-grade egress proxy.
ALLOW="$(docker run --rm "${LOCKS[@]}" --network none \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} ${PASS_ENV[@]+"${PASS_ENV[@]}"} \
  --entrypoint python "$RUNNER_IMAGE" -m atoll_env.network --task /nonexistent \
  | sed -n 's/^allow=//p')"
printf 'allow=%s\n' "$ALLOW" >"$OUT/network.txt"

docker network inspect "$NETWORK" >/dev/null 2>&1 \
  || docker network create -o com.docker.network.bridge.enable_icc=false "$NETWORK" >/dev/null
docker network create --internal "$ID-net" >/dev/null
docker run -d --name "$ID-egress" "${LOCKS[@]}" \
  --network "$NETWORK" \
  --memory 64m --memory-swap 64m --cpus 0.5 --pids-limit 64 \
  -e ATOLL_EGRESS_ALLOW="$ALLOW" \
  --entrypoint python "$RUNNER_IMAGE" -m atoll_env.egress_proxy >/dev/null
docker network connect "$ID-net" "$ID-egress"
for _ in $(seq 1 50); do
  docker logs "$ID-egress" 2>&1 | grep -q " START " && break
  sleep 0.2
done
PROXY="http://$ID-egress:3128"

docker run --rm --name "$ID-judge" "${LOCKS[@]}" \
  --network "$ID-net" \
  -e "HTTPS_PROXY=$PROXY" -e "HTTP_PROXY=$PROXY" -e "https_proxy=$PROXY" -e "http_proxy=$PROXY" \
  -e "NO_PROXY=" -e "no_proxy=" -e "AIOHTTP_TRUST_ENV=true" \
  --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \
  --tmpfs /tmp:rw,size=128m,mode=1777 \
  -v "$OUT:/grade" \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} \
  --entrypoint atoll-grade "$RUNNER_IMAGE" \
  --from-rendered /grade --judge-model "$MODEL" --output /grade/score.json \
  ${EXTRA[@]+"${EXTRA[@]}"}
