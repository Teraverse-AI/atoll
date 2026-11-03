# environment

Workspace lifecycle for Atoll runs — the lightweight counterpart of Archipelago's
`environment/`. No FastAPI, no `/mcp` endpoint, no sockets: a library (`atoll_env`)
plus a thin CLI.

- `workspace.py` — copy a task's `workspace/` template into a fresh run directory
- `snapshot.py` — tar.gz a workspace's state (initial/final, diffable by graders)
- `sandbox.py` — the **required** Docker sandbox for the code-execution tools
  (`python_code_execution`, `code_exec`): `--network none`, memory-capped,
  workspace-only mount, image `atoll-sandbox:latest` (Archipelago's package profile,
  built by `scripts/build_sandbox_image.sh`); no unsandboxed fallback. Two modes:
  per-call `docker run --rm` for host runs, or — when `ATOLL_SANDBOX_CHANNEL` is
  set — the eval's long-lived companion sandbox (below)
- `sandbox/` — the `atoll-sandbox` image: package profile plus `sandbox_server.py`,
  the companion server (PID 1; every call gets a clean slate: other processes
  killed, `/tmp` emptied)
- `isolation_probe.py` — checks an eval's container pair from the inside (used by
  `tests/test_isolation.py`)
- `main.py` — workspace CLI (`atoll-workspace`): `python -m atoll_env.main prepare|snapshot ...`

## Containerized evals

The machine needs only Docker (no Python install). Each eval runs as an isolated
set of containers that shares nothing with any other eval:

| Container | Image | Network | Sees |
|---|---|---|---|
| `<id>-runner` | `atoll-runner` (loop + LLM layer + tools; `agents/Dockerfile`) | per the `network` setting (below); never other evals | task bundle (read-only), its run dir, its channel |
| `<id>-egress` | `atoll-runner` (`atoll_env.egress_proxy`) — only when `network` is off | bridges the runner's internal network to the allow-listed endpoint(s) only | nothing (no mounts) |
| `<id>-sandbox` | `atoll-sandbox` (model-written code) | **none**, always | its workspace, its channel |

Code calls travel as files through a private per-eval volume (`<id>-ch`); no
container gets the Docker socket, root, capabilities or a writable root
filesystem, and each is memory/CPU/process capped. Credentials go to the runner
only. `scripts/run_eval.sh` is the reference launcher. Deploy with your own pipeline —
a UI, a server, CI, or anything else — by issuing the same `docker` commands:

```bash
scripts/build_sandbox_image.sh && scripts/build_runner_image.sh
scripts/run_eval.sh --task examples/q001_adjusting_entry --out runs/q001/eval1 \
    --model <model> --env-file .env
```

Isolation is verified, not assumed — N evals in parallel, each probing both of
its containers:

```bash
ATOLL_ISOLATION_TESTS=1 uv run pytest environment/tests/test_isolation.py -v
```

### Network setting

`network` is resolved like the loop's stop conditions (`max_steps`, …):
`--network` flag > `task.toml` `[atoll] network` > `$ATOLL_NETWORK` > default **`off`**.

| `network` | Runner can reach | How it is enforced |
|---|---|---|
| **`off`** (default) | only the allow-listed LLM endpoint(s) | the runner sits on a Docker `--internal` network with no route out; its only exit is the per-eval egress proxy, which admits only allow-listed `host:port` pairs (HTTPS tunnels end-to-end — the proxy never sees requests or keys) and refuses everything else with 403 |
| `on` | ordinary outbound access | shared runner network; still no traffic to other evals' containers |

- **Allow-list**: `ATOLL_EGRESS_ALLOW` (comma-separated `host:port`) if set, else the
  host of `LITELLM_BASE_URL`. Direct-to-provider runs must list provider hosts
  explicitly (e.g. `api.openai.com:443`); with nothing to allow, the launcher refuses
  to start. A gateway on the host machine is reached as `host.docker.internal:<port>`.
- **Audit**: every proxy decision lands in `<run-dir>/egress.log`; the resolved setting
  in `<run-dir>/network.txt`.
- **The code sandbox never has a network**, whatever `network` says.
- Host runs (`uv run atoll-run`) are not containerized and use the host's network.

**This is not Mercor's mechanism.** The APEX-Agents paper (Vidgen et al., 2026,
[arXiv:2601.14242](https://arxiv.org/abs/2601.14242), §3.1) states: *"Web search is
turned off for the benchmark to keep evaluations reproducible."* We found no
container-level egress restriction in Archipelago's open-source repo; any network
policy of Mercor's hosted runs is not public. Atoll's interpretation: turning off web
search only means something if the agent cannot fetch outside information at all —
an agent with open network access could retrieve whatever it can reach — so Atoll
enforces the intent at the network level, allowing nothing but the model endpoint.
