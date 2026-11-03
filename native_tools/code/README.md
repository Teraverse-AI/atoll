# code

Native port of Archipelago's [`mcp_servers/code`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/code) (`code_execution_server`), model-visible surface verbatim.

| Tool | Does |
| --- | --- |
| `python_code_execution` | Run Python 3 code; returns stdout/stderr/exit code; description names the preinstalled package profile. |
| `code_exec` | Run a shell command; upstream's output heuristics (binary-dump suppression, HTML suppression, head/tail truncation) ported verbatim. |

**Execution is ALWAYS sandboxed** — never on the host: a short-lived
`--network none`, memory-capped Docker container with only the task
workspace mounted. The image (`atoll-sandbox:latest`, built by
`scripts/build_sandbox_image.sh` from `environment/sandbox/`) preinstalls the same
package profile as Archipelago's code server, and the tool description lists those
packages exactly as upstream does.

Package: `code_execution_server/` · Tests: `tests/` (container tests self-skip when
no working sandbox is present).
