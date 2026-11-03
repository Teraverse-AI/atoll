#!/usr/bin/env bash
# Run ONE Atoll eval as an isolated container pair. This is the reference launcher:
# deploy with your own pipeline (a UI, a server, CI, ...) by issuing the same docker
# commands with the same flags.
#
# Usage:
#   scripts/run_eval.sh --task <task-dir> --out <run-dir> --model <model> \
#       [--env-file <file>] [--network on|off] [-- <runner args...>]
#
#   <task-dir>  Harbor-style bundle (task.toml, instruction.md, workspace/, gold_answer.json)
#   <run-dir>   must not exist yet; receives workspace/, snapshots, trajectory.jsonl, result.json
#   --env-file  model credentials etc. — given to the RUNNER only, never the sandbox
#   --network   the runner's network (like --max-steps: CLI > task.toml [atoll].network
#               > $ATOLL_NETWORK > default off). off = only the allow-listed LLM
#               endpoint ($ATOLL_EGRESS_ALLOW, else the LITELLM_BASE_URL host), via the
#               eval's egress proxy; on = ordinary outbound access. Never the sandbox.
#   -- ...      extra atoll-run flags (e.g. --max-steps 50). ATOLL_RUNNER_ENTRYPOINT
#               overrides the runner's entrypoint (used by the isolation tests).
#
# Per eval, containers that share nothing with any other eval:
#   <id>-runner   loop + tools · sees: task (read-only), its run dir, its channel
#                 network off: only <id>-net (Docker --internal: no route out) → proxy
#                 network on:  shared runner network, no container-to-container traffic
#   <id>-egress   (network off only) allow-list proxy bridging <id>-net and the outside;
#                 its decisions are saved to <run-dir>/egress.log
#   <id>-sandbox  model-written code · NO network · sees: its workspace, its channel
#   <id>-ch       private named volume carrying code calls between runner and sandbox
# No container gets the Docker socket, root, capabilities, or a writable root
# filesystem; all are memory/CPU/process capped. Everything is removed on exit.
set -euo pipefail

RUNNER_IMAGE="${ATOLL_RUNNER_IMAGE:-atoll-runner:latest}"
SANDBOX_IMAGE="${ATOLL_SANDBOX_IMAGE:-atoll-sandbox:latest}"
NETWORK="${ATOLL_RUNNER_NETWORK:-atoll-runner-net}"

TASK="" OUT="" MODEL="" ENV_FILE="" NETWORK_CLI="" EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --task) TASK="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --network) NETWORK_CLI="$2"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$TASK" ] && [ -n "$OUT" ] || { echo "usage: $0 --task DIR --out DIR --model M [--env-file F] [-- args]" >&2; exit 2; }
[ -n "$MODEL" ] || [ -n "${ATOLL_RUNNER_ENTRYPOINT:-}" ] || { echo "--model is required" >&2; exit 2; }
[ -d "$TASK" ] || { echo "task dir not found: $TASK" >&2; exit 2; }
[ ! -e "$OUT" ] || { echo "run dir already exists: $OUT" >&2; exit 2; }

TASK="$(cd "$TASK" && pwd)"
mkdir -p "$OUT/workspace"
OUT="$(cd "$OUT" && pwd)"
ID="atoll-$(date +%s)-$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"

cleanup() {
  if docker container inspect "$ID-egress" >/dev/null 2>&1; then
    docker logs "$ID-egress" >"$OUT/egress.log" 2>&1 || true
  fi
  docker rm -f "$ID-sandbox" "$ID-runner" "$ID-egress" >/dev/null 2>&1 || true
  docker network rm "$ID-net" >/dev/null 2>&1 || true
  docker volume rm -f "$ID-ch" >/dev/null 2>&1 || true
}
trap cleanup EXIT

LOCKS=(--read-only --cap-drop ALL --security-opt no-new-privileges --user 1000:1000)
ENV_ARGS=()
[ -n "$ENV_FILE" ] && ENV_ARGS=(--env-file "$ENV_FILE")
# Host-level overrides pass through when set (an env-file may also carry them).
PASS_ENV=()
for v in ATOLL_NETWORK ATOLL_EGRESS_ALLOW; do
  [ -n "${!v:-}" ] && PASS_ENV+=(-e "$v=${!v}")
done

# Resolve the network setting + allow-list in a throwaway, network-less runner
# (same precedence logic as the loop's stop conditions; see atoll_env/network.py).
PLAN="$(docker run --rm "${LOCKS[@]}" --network none \
  -v "$TASK:/task:ro" ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} ${PASS_ENV[@]+"${PASS_ENV[@]}"} \
  --entrypoint python "$RUNNER_IMAGE" -m atoll_env.network --task /task \
  ${NETWORK_CLI:+--network "$NETWORK_CLI"})"
NET_MODE="$(printf '%s\n' "$PLAN" | sed -n 's/^network=//p')"
ALLOW="$(printf '%s\n' "$PLAN" | sed -n 's/^allow=//p')"
printf 'network=%s\nallow=%s\n' "$NET_MODE" "$ALLOW" >"$OUT/network.txt"

# Shared runner network with inter-container traffic disabled (network on: runners'
# outbound route; network off: the egress proxies' outbound route).
docker network inspect "$NETWORK" >/dev/null 2>&1 \
  || docker network create -o com.docker.network.bridge.enable_icc=false "$NETWORK" >/dev/null

docker volume create "$ID-ch" >/dev/null

RUNNER_NET=(--network "$NETWORK")
if [ "$NET_MODE" = "off" ]; then
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
  RUNNER_NET=(--network "$ID-net"
    -e "HTTPS_PROXY=$PROXY" -e "HTTP_PROXY=$PROXY" -e "https_proxy=$PROXY" -e "http_proxy=$PROXY"
    -e "NO_PROXY=" -e "no_proxy=" -e "AIOHTTP_TRUST_ENV=true")
fi

docker run -d --name "$ID-sandbox" "${LOCKS[@]}" \
  --network none \
  --memory 512m --memory-swap 512m --cpus 1 --pids-limit 128 \
  --tmpfs /tmp:rw,size=256m,mode=1777 \
  -v "$OUT/workspace:/workspace" \
  -v "$ID-ch:/channel" \
  "$SANDBOX_IMAGE" >/dev/null

RUNNER_ARGS=(--task /task --output /eval/run --workspace /eval/run/workspace)
[ -n "$MODEL" ] && RUNNER_ARGS+=(--model "$MODEL")
ENTRY=()
if [ -n "${ATOLL_RUNNER_ENTRYPOINT:-}" ]; then
  ENTRY=(--entrypoint "$ATOLL_RUNNER_ENTRYPOINT"); RUNNER_ARGS=()
fi

docker run --rm --name "$ID-runner" "${LOCKS[@]}" \
  "${RUNNER_NET[@]}" \
  --memory 1g --memory-swap 1g --cpus 2 --pids-limit 256 \
  --tmpfs /tmp:rw,size=512m,mode=1777 \
  -v "$TASK:/task:ro" \
  -v "$OUT:/eval/run" \
  -v "$ID-ch:/channel" \
  -e ATOLL_EVAL_ID="$ID" -e ATOLL_NETWORK_MODE="$NET_MODE" \
  ${ENV_ARGS[@]+"${ENV_ARGS[@]}"} ${PASS_ENV[@]+"${PASS_ENV[@]}"} \
  ${ENTRY[@]+"${ENTRY[@]}"} \
  "$RUNNER_IMAGE" ${RUNNER_ARGS[@]+"${RUNNER_ARGS[@]}"} ${EXTRA[@]+"${EXTRA[@]}"}
