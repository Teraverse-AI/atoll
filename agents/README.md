# agents

The Atoll agent harness. `runner/loop.py` is a faithful port of Archipelago's
`loop_truncated_tools_agent` (Apache-2.0, see NOTICE): the model calls tools in a
loop and the run finalizes on the first reply with no tool calls. Each ported
module's docstring lists its divergences from upstream.

## Vendored upstream layer

The LLM call layer is not a port — it is Archipelago's own code, vendored
byte-identical at a pinned commit under `runner/utils/` (`llm.py` and its
transitive dependencies: image/PDF policies, Gemini schema preservation,
retry decorators, context budget, usage tracking, litellm patches).
`runner/llm.py` is only a thin adapter re-exporting it under the names the
loop and tests use. The full provider-specific request surgery (Anthropic
empty-text/prefill/image handling, Gemini trailing-user-turn normalization
and tool unions, Fireworks `tool_choice` stripping, Harmony recovery,
prompt-cache breakpoints, split Responses tool-choice merging) therefore runs
exactly as in upstream's harness, in both routing modes. Upstream's infra
buckets (metrics, Redis spend guardrail, CAS ledger, gateway routing,
streaming telemetry) are vendored-but-disabled via same-path shims.
`runner/utils/PATCHES.md` holds the pinned commit, the per-file sha256
manifest, the shim list and the resync procedure.

## Atoll-local provider fixes (`runner/atoll_fixes.py`)

The vendored surgery covers the providers upstream used. Providers it never
called get their fixes in this separate Atoll-owned layer, applied by the
`runner/llm.py` adapter around the vendored call — vendored bytes stay
untouched and resyncable. Currently: **Volcengine ARK thinking models
(DeepSeek V4 family)**, whose thinking tokens share the `max_tokens` budget —
small budgets come back as well-formed but *empty* answers. The fix raises a
sub-16384 `max_tokens`, lifts the per-call timeout to ≥900 s (minutes of
silent thinking are normal), and treats an empty-content/large-reasoning reply
as retryable — up to two retries, doubling the budget each time; the model's
configuration (thinking on) is never changed, so every scored answer is
attributable to one setup — instead of letting the loop finalize "No content".
Each retry is logged. Per-call opt-out: `extra_args={"atoll_fixes": false}`.

## Direct-to-provider mode

With `LITELLM_BASE_URL` / `LITELLM_API_KEY` set, requests go to the configured
OpenAI-compatible endpoint (e.g. a LiteLLM proxy). With `LITELLM_BASE_URL`
unset, no `api_base` is injected and litellm routes each call direct to the
provider: use provider-prefixed model names (`gemini/gemini-2.5-pro`,
`anthropic/claude-sonnet-4-5`, `volcengine/...`) and set the matching provider
API keys in the environment (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `VOLCENGINE_API_KEY`, ...). See `.env.example`.

## Data flow

```
task.toml + instruction.md
        │
        ▼
runner/main.py ── create_workspace() ──► workspace copy (atoll_env)
        │   builds AgentRunInput{initial_messages, workspace_dir, agent_config_values}
        ▼
runner/loop.py (LoopTruncatedToolsAgent, async)
        │  ◄──► runner/llm.py (litellm.acompletion / aresponses → any OpenAI-compatible endpoint)
        │  ◄──  runner/responses.py (chat ↔ Responses API conversion, output parsing)
        │  ◄──  runner/utils/context_budget.py (trim), runner/utils/usage.py (usage)
        │  tools=provider.list_tools()
        ▼
runner/providers/ToolProvider          ◄── the ONLY tool interface the loop knows
        ├── NativeToolProvider          ── atoll_tools registry (production default)
        └── McpToolProvider             ── Archipelago gateway (stub)
        │
        ▼
trajectory.jsonl + result.json + snapshots
```

## System prompt

Default: `runner/system_prompts/atoll_agents.md` — Atoll's own, editable; it started
as a copy of Mercor's APEX-Agents prompt, kept verbatim beside it as a reference
(`apex_agents.md`; see that folder's README for provenance).
Overrides, per-task first: task.toml `[atoll] system_prompt_file` (relative to the
task dir) > `--system-prompt-file` > `$ATOLL_SYSTEM_PROMPT_FILE` > default. The CLI
prints which one a run used; the trajectory's first message records the text.

## Office tools (opt-in)

Atoll's LibreOffice-backed tools (`recalculate_spreadsheet`, `convert_document_to_pdf`;
see [native_tools](../native_tools/README.en.md#highlight-the-libreoffice-office-layer))
are **off by default**, so a run's tool surface is Archipelago's. Turn them on with
`--office-tools on` > task.toml `[atoll] office_tools = true` > `$ATOLL_OFFICE_TOOLS` >
off (CLI wins, like the stop conditions). The CLI prints the setting; a run with them on
is not directly comparable with an Archipelago run.

## Stop conditions

Config keys live in `task.toml`'s `[agent]` table (upstream's exact names;
CLI flags `--max-steps/--timeout/--token-budget` and env
`MAX_STEPS/TIMEOUT/TOKEN_BUDGET` override). The CLI applies Atoll's harness
defaults (`max_steps=250`, `timeout=3600`, token budget off); the loop class
itself keeps upstream's internal defaults, shown here:

| Condition / key      | Default | Behavior                                              |
| -------------------- | ------- | ----------------------------------------------------- |
| final answer         | —       | first reply with no tool calls → `completed`          |
| `max_steps`          | 100     | not finalized in time → `failed`                      |
| `timeout` (wall, s)  | 10800   | → `error`, fault `budget_exhausted/wall_clock`        |
| `token_budget`       | 0 (off) | once spent, one final warned turn, then `failed`      |
| `cost_budget_usd`    | 0 (off) | same, in $ (`accounting_mode = "cost_accounting"`)    |
| empty replies        | 4       | nudged with "continue", then abandoned (`failed`,     |
|                      |         | fault `empty_response`)                               |
| reasoning-only turns | 4       | nudged to act/answer, then abandoned                  |

Other per-step guards (all upstream keys): `tool_call_timeout` (60s, timed-out
call becomes a "Tool call timed out" observation), `llm_response_timeout`
(600s), `max_output_lines`/`max_output_chars` (200 / 32,768 — every tool result
is truncated before entering context), `turn_warnings_enabled`,
`disabled_tools`.

### Responses API (reasoning models)

A `responses/` segment in the model name (e.g. `openai/responses/gpt-x`)
routes LLM calls through the Responses API (`litellm.aresponses`); the
chat-shaped history and tools are converted by `runner/responses.py` and the
output is mapped back, so the loop itself (trim, budgets, tool execution) is
unchanged. `replay_reasoning = true` keeps encrypted reasoning items on the
stored assistant turn and replays them on the next request; a reasoning-only
turn counts toward the reasoning-only nudge/abandon limit. With
`litellm_routes_responses = true` the prefixed name is instead sent whole over
Chat Completions, and the configured OpenAI-compatible endpoint (e.g. a
LiteLLM proxy) resolves it to the Responses API server-side.

### Context budget

With `context_trim_utilization > 0`, the history is measured before every
dispatch (litellm estimate calibrated against provider-reported prompt
tokens); once the projection exceeds that fraction of the model's input
window, the oldest assistant+tool exchanges are dropped down to
`context_trim_floor` (default 0.60) and a trim notice is injected so the
model knows content disappeared. A provider context-window 400 triggers the
same trim and the step is retried.

With `max_step_tool_result_tokens > 0`, each step's tool-result batch is
priced; a result that would blow the budget is refused with a 400-char
preview — or, with `spill_oversized_results = true`, written whole into the
run workspace and replaced by a notice carrying its `/`-rooted path
(readable via `read_text_file` or from `python_code_execution`).

## Running

```bash
uv run atoll-run --task <task-dir> --model <model>
```

## trajectory.jsonl format

One OpenAI-shaped message object per line (`{"index": N, "role": ..., ...}`),
followed by a single `{"type": "result", ...}` line with status, time_elapsed,
usage (token totals + per-call log), final_answer, error, and fault.
