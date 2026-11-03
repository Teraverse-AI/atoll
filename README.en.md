# Atoll

[简体中文](./README.md) | **English**

***A**gentic **T**ask **O**ntology for **LL**M*

<a href="LICENSE"><img src="https://img.shields.io/badge/📜-Apache%202.0-b31b1b"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/🐍-Python%203.12+-yellow"></a>
<a href="https://github.com/Mercor-Intelligence/archipelago"><img src="https://img.shields.io/badge/🏝️-Archipelago%20compatible-0ea5e9"></a>
<a href="mailto:dev@teravs.com"><img src="https://img.shields.io/badge/✉️-Contact-green"></a>

**Atoll** is a lightweight system for running and evaluating AI agents on professional, tool-using tasks — currently focused on accounting and finance — by Teraverse Limited. It is a drop-in-comparable counterpart to Mercor's [Archipelago](https://github.com/Mercor-Intelligence/archipelago): same architectural boundaries, same model-visible tool surface, a fraction of the footprint. It consists of three main components:

1. **Environment**: Workspace lifecycle (populate, snapshot) — a plain Python library, no gateway process
2. **Agents**: Extensible agent runner with a registry of configurable agent implementations
3. **Grading**: Grades agent performance by comparing before/after snapshots *(deferred — see [grading/README.md](./grading/README.md))*

**Design philosophy — comparability through reuse, not reimplementation.** A model inside a run cannot tell Atoll from Archipelago, and scores are directly comparable, because everything the model can observe is Mercor's own, not an imitation:

- **Tool surface**: tool names, schemas, descriptions, and result/error formats ported verbatim from Archipelago's MCP servers (same gateway naming) — but served as native in-process Python functions, no MCP gateway, no stdio server fleet.
- **Agent loop**: a faithful port of Mercor's `loop_truncated_tools_agent` — context trimming, tool-result budgets with refusal/spill, token/cost accounting, byte-identical message templates.
- **LLM layer**: Mercor's `runner/utils/llm.py` and helpers **vendored byte-identical** at a pinned upstream commit — full provider-specific request surgery, retry policy, cache breakpoints — with their server infrastructure switched off via thin shims, never rewritten. Upstream improvements are adopted by re-syncing, not re-porting.

Model calls route either through an OpenAI-compatible proxy **or directly to official provider endpoints** — the vendored surgery layer makes both modes behave identically, exactly as in Mercor's harness. Only `python_code_execution`/`code_exec` touch a container: model-written code always runs in a network-less Docker sandbox with the same preinstalled package profile as Archipelago's code server.

## Table of Contents

- [Quick Start: Run Your First Task](#quick-start-run-your-first-task)
- [Components](#components)
  - [Environment](#environment)
  - [Native Tools](#native-tools)
  - [Agents](#agents)
  - [Grading](#grading)
- [Atoll vs. Archipelago](#atoll-vs-archipelago)
- [Local Development](#local-development)
- [License](#license)

---

## Quick Start: Run Your First Task

**Estimated time: ~10 minutes**

### Prerequisites

- Docker — always: model-written code only ever runs in a container
- [uv](https://docs.astral.sh/uv/) — for this from-source Quick Start only; it installs
  Python 3.12 itself if you don't have it (no separate Python install needed)
- Model access, either way:
  - an OpenAI-compatible endpoint (e.g. a LiteLLM proxy) and its API key, **or**
  - direct-to-provider: official API keys for the providers you use

> Only running evals, not developing? You need **Docker alone** — no uv, no Python:
> build the two images and launch each eval with `scripts/run_eval.sh` (or your own
> pipeline). See [Containerized evals](./environment/README.md#containerized-evals).

### 1. Set Up Environment Variables

```bash
cd atoll
uv sync
cp .env.example .env
# Proxy mode — edit .env:
#   LITELLM_BASE_URL=https://your-litellm-host/
#   LITELLM_API_KEY=sk-...
#   MODEL_ALIAS=gpt-4o-mini
# Direct mode — leave LITELLM_BASE_URL empty and set provider keys instead
#   (OPENAI_API_KEY=..., GEMINI_API_KEY=..., VOLCENGINE_API_KEY=..., ...);
#   model names are then litellm provider-prefixed, e.g. "gemini/gemini-2.5-pro".
```

### 2. Build the code-execution sandbox image (one-time)

```bash
scripts/build_sandbox_image.sh
# → atoll-sandbox:latest, preinstalled with the same package profile
#   (numpy, pandas, scipy, statsmodels, sympy, …) as Archipelago's code server
```

### 3. Run an Example

```bash
uv run atoll-run --task examples/q001_adjusting_entry --model gpt-4o-mini
```

The runner will:
1. Copy the task's workspace template into a fresh run directory
2. Snapshot the initial state
3. Mount the native tool servers and run the agent loop
4. Snapshot the final state
5. Write the trajectory and result

### 4. Check Results

```bash
RUN=$(ls -td runs/q001_adjusting_entry/* | head -1)
cat $RUN/result.json | jq .status
cat $RUN/trajectory.jsonl | tail -1 | jq .
ls $RUN   # workspace/ initial_snapshot.tar.gz final_snapshot.tar.gz trajectory.jsonl result.json
```

---

## Components

### Environment

The Environment is a plain Python library (`atoll_env`) plus a thin CLI — Archipelago's headless FastAPI gateway, S3 population, and `/mcp` endpoint are intentionally absent.

| Archipelago | Atoll |
|---|---|
| `POST /data/populate` (from S3) | `atoll_env.workspace.create_workspace()` (from the task's `workspace/` template) |
| `POST /data/snapshot` (tar.gz) | `atoll_env.snapshot.snapshot_workspace()` (same tar.gz layout) |
| `POST /apps` (hot-swap MCP config) | `load_tools([...])` — pick which tool servers a run mounts |
| MCP gateway process | — (tools are in-process functions) |

`atoll_env.sandbox.ContainerSandbox` is the **required** container sandbox for model-written code: Docker Desktop; `--network none`, memory-capped, only the workspace mounted. There is no unsandboxed fallback. For containerized evals (only Docker needed on the machine, no Python install), each eval runs as an isolated runner + sandbox container pair (runner network **off** by default: only the LLM endpoint is reachable) that you can launch from your own pipeline — a UI, a server, CI, or anything else; see [Containerized evals](./environment/README.md#containerized-evals).

> For more details, see the [Environment README](./environment/README.md).

### Native Tools

`native_tools/` replaces Archipelago's `mcp_servers/` — the only structural change in the layout. It mirrors Archipelago's 11 server modules one-to-one: each module is a subfolder with its importable server package, its own `tests/`, and a README. Implemented modules preserve the upstream tool names, parameter schemas, descriptions, and output text verbatim; the rest are README placeholders describing the original server:

```
native_tools/
├── core/              # atoll_tools package: registry, schema generation, workspace context
├── filesystem/        # ✅ filesystem_server — 6 tools
├── spreadsheets/      # ✅ sheets_server — all 12 tools incl. edit_spreadsheet, filter_tab
├── pdfs/              # ✅ pdf_server — 5 tools (PyMuPDF + reportlab)
├── code/              # ✅ code_execution_server — python_code_execution, code_exec (sandboxed)
├── documents/         # ✅ docs_server — all 15 tools (python-docx)
├── calendar/ chat/ edgar_sec/ fmp/ mail/ presentations/   # 📄 README placeholders
├── README.md          # module index with per-tool descriptions (中文)
└── README.en.md       # English version
```

Tool names observed by the model follow FastMCP's gateway rule, exactly like Archipelago: with several servers mounted, tools appear as `{server_name}_{tool_name}` (e.g. `filesystem_server_list_files`). Each ported server also vendors the exact `tools/list` JSON Archipelago's gateway served at the pinned upstream commit (`published_schemas.json`) and serves it verbatim, so parameter schemas — including upstream's flattening, `(Optional)`/nullable annotations, and nested `input` envelopes — are byte-identical; parity is pinned by tests.

> For the full tool-parity matrix and the list of Archipelago servers not (yet) ported, see the [Native Tools README](./native_tools/README.en.md).

### Agents

The Agents component mirrors Archipelago's `agents/runner/`. The core loop (`agents/runner/loop.py`) is adapted from Mercor's `loop_truncated_tools_agent` (Apache-2.0): tool-calling loop, 200-line/32K-char tool-result truncation, finalize on the first reply without tool calls. The LLM layer underneath is Mercor's own code, vendored byte-identical at a pinned upstream commit (provider-specific request surgery included; unused infra disabled via shims — see `agents/runner/utils/PATCHES.md`).

```
┌────────────────────────────────────────────────────────────┐
│                       Agents Runner                        │
│  runner/                                                   │
│  ├── main.py          CLI: load task, run loop, artifacts  │
│  ├── loop.py          Core loop (from Mercor's loop agent) │
│  ├── llm.py           LiteLLM (chat + Responses API) →     │
│  │                      any OpenAI-compatible endpoint     │
│  ├── responses.py     Chat ↔ Responses API conversion      │
│  ├── models.py        AgentRunInput / AgentTrajectoryOutput│
│  ├── registry.py      Agent registry                       │
│  └── providers/       ToolProvider boundary                │
│      ├── native.py    in-process registry (default)        │
│      └── mcp.py       MCP gateway interop (stub)           │
└────────────────────────────────────────────────────────────┘
```

`loop.py` depends only on the `ToolProvider` interface — swapping `NativeToolProvider` for a future `McpToolProvider` pointed at a real Archipelago gateway requires no loop changes.

**Stop conditions** — Atoll harness defaults, overridable per task in `task.toml` or via CLI/env: `max_steps=250`, `timeout=3600s`, token budget off (when set, budget exhaustion grants one final warned turn). The ported loop itself keeps upstream's internal defaults (`100`/`10800s`/off) for library use; the example tasks in `examples/` set their own values in `task.toml`.

> For more details, see the [Agents README](./agents/README.md).

### Grading

Deferred. The contract it will consume already exists (initial/final snapshots, `trajectory.jsonl`, `gold_answer.json`, `[[atoll.verifiers]]` in `task.toml`); see [grading/README.md](./grading/README.md).

---

## Atoll vs. Archipelago

| | Archipelago | Atoll |
| --- | --- | --- |
| Tool transport | MCP gateway + stdio server per tool family | native Python functions, in-process |
| Model-visible tool surface | MCP servers via OpenAI function calling | identical (ported verbatim, same gateway naming) |
| RAM overhead per task | ~400MB (MCP gateway + server processes) | ~0 (one Python process) |
| Containers | Docker for the whole environment | host runs: Docker only for code sandboxing; containerized evals: per eval a runner + network-less sandbox (+ egress proxy when `network` is off) |
| Network | the [APEX-Agents paper](https://arxiv.org/abs/2601.14242) states: *"Web search is turned off for the benchmark to keep evaluations reproducible"* (§3.1); we found no container-level egress restriction in the open-source repo | **not Mercor's mechanism** — Atoll reads "no web search" as "no outside information" (open network access would let an agent fetch whatever it can reach), so containerized evals have a `network` setting (like `max_steps`), default **off**: the runner reaches only the allow-listed LLM endpoint via a per-eval egress proxy; the code sandbox never has a network ([details](./environment/README.md#network-setting)) |
| Task format | Harbor bundles (`task.toml`, `instruction.md`, snapshots) | same layout |
| Agent loop | `loop_truncated_tools_agent` | faithful port: trimming, budgets, spill, byte-identical templates (see NOTICE) |
| LLM client layer | `runner/utils/llm.py`: provider request surgery, retries, cache breakpoints | the same files, vendored byte-identical at a pinned commit; server infra disabled via shims (`agents/runner/utils/PATCHES.md`) |
| Model routing | direct-to-provider or via their proxy | identical: proxy or direct, same surgery either way |
| Retry ownership | harness client owns retries; proxy pass-through | same (gateway deployments for Atoll should pin retries to 0) |
| Repo layout | `mcp_servers/` | renamed `native_tools/`; otherwise mirrored |
| License | Apache-2.0 | Apache-2.0 |

---

## Local Development

```bash
uv sync                                    # install the uv workspace (3 packages)
uv run pytest                              # run the test suite
uv run ruff check .                        # lint
uv run python scripts/generate_task_workspaces.py   # regenerate task fixtures (xlsx/pdf)
```

Example tasks live in `examples/` as Harbor-style bundles (`task.toml` + `instruction.md` + `workspace/` + `gold_answer.json`) — a small sample, not the full task set; see [examples/README.md](./examples/README.md).

Containerized evals: `scripts/build_runner_image.sh` + `scripts/build_sandbox_image.sh`, then `scripts/run_eval.sh` (one isolated container pair per eval; see the [Environment README](./environment/README.md#containerized-evals)).

---

## License

Apache-2.0 (see [LICENSE](./LICENSE)).

Portions are adapted from or vendored verbatim out of Mercor Intelligence's [Archipelago](https://github.com/Mercor-Intelligence/archipelago) (Apache-2.0) — notably `agents/runner/loop.py`, the LLM layer under `agents/runner/utils/` (byte-identical files listed in `agents/runner/utils/PATCHES.md`), and the tool implementations in `native_tools/`'s server modules — see [NOTICE](./NOTICE). Atoll's PDF tools depend on [PyMuPDF](https://pymupdf.readthedocs.io/) (AGPL-3.0), the same engine Archipelago's PDF server uses: Atoll's own code remains Apache-2.0, while PyMuPDF's license terms apply to the operator of the combined work — fine for internal evaluation use; organizations redistributing a service built on Atoll should review AGPL obligations or obtain Artifex's commercial license.
