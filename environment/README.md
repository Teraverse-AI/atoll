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
**pair** that shares nothing with any other eval:

| Container | Image | Network | Sees |
|---|---|---|---|
| `<id>-runner` | `atoll-runner` (loop + LLM layer + tools; `agents/Dockerfile`) | LLM egress only; no container-to-container traffic | task bundle (read-only), its run dir, its channel |
| `<id>-sandbox` | `atoll-sandbox` (model-written code) | **none** | its workspace, its channel |

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
