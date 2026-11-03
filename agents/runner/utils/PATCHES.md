# Vendored upstream LLM layer — manifest

Upstream: [Mercor-Intelligence/archipelago](https://github.com/Mercor-Intelligence/archipelago)
(Apache-2.0), pinned at commit `f8e41cbb6f6158a0f3fbe81a74ecfdcf70fbfcc3`.

Atoll's LLM call layer is upstream's own code, vendored **byte-identical** at
the same package paths wherever its imports can be satisfied. Modules that
would drag heavy or private infrastructure (Datadog, Redis, the CAS client,
the MCP gateway, server settings) are satisfied instead by thin same-path,
same-signature **shims** that hold the vendored code in its "disabled /
unconfigured" state. Nothing is subtracted from vendored files.

## Vendored verbatim

Each file below is byte-identical to the upstream file at the pinned commit
(`sha256` of both copies; re-verify with `shasum -a 256`).

| Upstream path (in archipelago) | Local path (in atoll) | sha256 |
|---|---|---|
| `agents/runner/utils/llm.py` | `agents/runner/utils/llm.py` | `83ad82881de6a1b15939bfd22cdd1a4969990d42fb721940deac183d2ea1c1c7` |
| `agents/runner/utils/gemini_schema.py` | `agents/runner/utils/gemini_schema.py` | `01e3de1bc8c5e2fc031e1e967e99d54b7353f2faf05f663e4e2b39721e2d6f1a` |
| `agents/runner/utils/image_fetch.py` | `agents/runner/utils/image_fetch.py` | `befb48fc181f88336d2db4f0ac990c5ad10a0d0df681901479a0c8416ad131ad` |
| `agents/runner/utils/pdf_text.py` | `agents/runner/utils/pdf_text.py` | `9e491f22f81e92091b629241c2d842318a13773dfbff336e38610fc72fb5663a` |
| `agents/runner/utils/litellm_patches.py` | `agents/runner/utils/litellm_patches.py` | `beac6bfec189341dfc11dbeb3af3af3256a07f9c649be5a5ac317299e28cebbb` |
| `agents/runner/utils/text_only_models.py` | `agents/runner/utils/text_only_models.py` | `bd1660860d8c9bb663b7b07f4eecbb2419f3307360931a2ac84f60784e8d010e` |
| `agents/runner/utils/decorators.py` | `agents/runner/utils/decorators.py` | `1b5aff33f30132b238d0e32a1c97a17de43455e7bb584e38a31d3a1b7dc409d8` |
| `agents/runner/utils/cas_ledger_emit.py` | `agents/runner/utils/cas_ledger_emit.py` | `f019061cd012f17e26f63b8f4507fc19c454932a468eac5c9bb7f428bdf2e5e3` |
| `agents/runner/utils/context_budget.py` | `agents/runner/utils/context_budget.py` | `27022c066de9715a8625a0df205d398c675ae334194dddb7dad92f498f181b75` |
| `agents/runner/utils/context_windows.py` | `agents/runner/utils/context_windows.py` | `2e647c520cfff7ed622d1bcfd659561cf23b547da30393b1f28d64bb342a3481` |
| `agents/runner/utils/usage.py` | `agents/runner/utils/usage.py` | `eb4f9753d67caf0f64e8a4553d5514aa2a359c1eadeccb2a840e66eee52467a3` |

Notes on self-disabling vendored files:

* `cas_ledger_emit.py` soft-imports its (unavailable) client package and
  degrades to a no-op — vendored verbatim, permanently disabled here.
* `pdf_text.py` imports `pymupdf` lazily inside a guarded helper; without it,
  PDF-to-text inlining logs a warning and passes the message through unchanged.
* `litellm_patches.py` gates each monkey-patch on the installed litellm
  version / attribute presence, so it is a no-op where the fix has shipped.

## Shims (same path, same signatures, infra turned OFF)

| Local path | Stands in for | Purpose |
|---|---|---|
| `runner/utils/metrics.py` | upstream Datadog metrics | no-op `distribution()` / `increment()` (upstream documents both as fire-and-forget, never-raising) |
| `runner/utils/settings.py` | upstream server settings | `get_settings()` exposing exactly what the vendored code reads; `apply_llm_target` sets `api_base`/`api_key` from `LITELLM_BASE_URL`/`LITELLM_API_KEY` (upstream's proxy branch, incl. the extra_body timeout forward) and leaves kwargs untouched when unset so litellm routes direct to the provider; `is_gateway_routed()` is always False; exports `work_unit_ctx` |
| `runner/utils/redis.py` | upstream Redis client | `redis_client = None` — the vendored guard already no-ops the budget meter on a None client |
| `runner/utils/budget_meter.py` | upstream Redis spend guardrail | upstream signatures (`read_state`/`hydrate_if_stale`/`accrue`, `BATCH_LANES`, `LANE_*`, `BudgetExceededError`, `BudgetSnapshot`) reproducing exactly the upstream `client is None` behavior; not vendorable verbatim because upstream imports the `redis` package at module level |
| `runner/utils/budget_hydration.py` | upstream meter-repair callback | async no-op `request_meter_hydration()` (never invoked: the meter shim never reports staleness) |
| `runner/utils/mcp.py` | upstream MCP gateway client | exposes only `tool_accepts_extra_headers` (+ its two pure helpers, copied verbatim) and `UnsupportedRequestFieldError`; the MCP session machinery is not vendored |
| `runner/utils/system_instructions.py` | upstream placement check | no-op `check_first_request()` (the upstream check context is never armed in Atoll) |
| `runner/agents/__init__.py`, `runner/agents/models.py` | upstream `runner.agents.models` | path shim re-exporting the message type aliases, `get_msg_role`/`get_msg_content`/`get_msg_attr` and `AgentConfigIds` (all copied verbatim); upstream's module also carries Studio report models not needed here |

## Reconciliation decisions

* `context_budget.py` / `context_windows.py` / `usage.py`: Atoll's earlier
  ported copies were replaced by the upstream originals, vendored verbatim —
  the loop's imports (`DEFAULT_TRIM_FLOOR`, `PromptTokenProjector`,
  `count_tokens`, `resolve_max_input_tokens`, `trim_to_budget`,
  `UsageTracker`) are signature-compatible, and `runner/utils/llm.py` needs
  `USAGE_IS_ESTIMATE_KEY`, which only the upstream original exports. The
  server rate-card tier (`model_rates_ctx`) is inert (its ContextVar is never
  set here); the checked-in context-window table is active, as upstream.
* `error.py` is NOT vendored: it imports the `mcp` client package (for
  gateway-session classification Atoll does not run). `is_system_error` stays
  as Atoll's ported classifier in `agents/runner/llm.py`, reusing the vendored
  `_is_non_retriable_bad_request` list.
* `agents/runner/llm.py` is a thin adapter re-exporting the vendored layer
  under the names the loop/tests/e2e already use, coercing
  `tools`/`extra_args` None to empty.

## What stays disabled, and how

* Datadog metrics → no-op shim functions.
* Batch-spend guardrail → `redis_client = None` + no-op meter shim
  (upstream's own fail-open path).
* CAS spend ledger → vendored file self-disables without its client package.
* Gateway routing → `is_gateway_routed()` always False (the vendored gateway
  branches are present but unreachable).
* Streaming → present and functional, off by default (`stream=False`), and it
  still self-enables for encrypted-reasoning Responses calls, as upstream.
* System-instructions placement check → no-op shim.

## Resync procedure

1. Clone upstream at the new commit:
   `git clone https://github.com/Mercor-Intelligence/archipelago && git -C archipelago checkout <commit>`.
2. Re-copy every file in the "Vendored verbatim" table from
   `archipelago/agents/<path>` over the local copy; update the pinned commit
   and the sha256 column (`shasum -a 256 <file>`), confirming local == upstream.
3. Re-check each vendored file's imports against the shim surfaces: grep the
   new `llm.py` (and friends) for `from runner.utils.settings|metrics|redis|
   budget_meter|budget_hydration|mcp|system_instructions import` and
   `from runner.agents.models import`, and extend the shims if upstream now
   reads new attributes (same-signature, disabled-state behavior only).
4. Run the suite from the repo root: `uv run pytest -q`,
   `uv run ruff check .`, and the e2e smoke script.
