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
| `runner/utils/settings.py` | upstream server settings | `get_settings()` exposing exactly what the vendored code reads; `apply_llm_target` routes per call: endpoint calls get `litellm_proxy/<model>` (as upstream's gateway branch, `_gateway_wire_model`) plus `api_base`/`api_key` from `LITELLM_BASE_URL`/`LITELLM_API_KEY` (upstream's proxy branch, incl. the extra_body timeout forward); calls with no endpoint or matching `ATOLL_DIRECT_MODELS` go direct (per-model settings from `runner/model_config.yaml`, applied by the adapter); `routes_via_endpoint()`; the adapter (`runner/llm.py`) switches off the `litellm.use_litellm_proxy` global the vendored module sets at import; `is_gateway_routed()` is always False; exports `work_unit_ctx` |
| `runner/utils/redis.py` | upstream Redis client | `redis_client = None` — the vendored guard already no-ops the budget meter on a None client |
| `runner/utils/budget_meter.py` | upstream Redis spend guardrail | upstream signatures (`read_state`/`hydrate_if_stale`/`accrue`, `BATCH_LANES`, `LANE_*`, `BudgetExceededError`, `BudgetSnapshot`) reproducing exactly the upstream `client is None` behavior; not vendorable verbatim because upstream imports the `redis` package at module level |
| `runner/utils/budget_hydration.py` | upstream meter-repair callback | async no-op `request_meter_hydration()` (never invoked: the meter shim never reports staleness) |
| `runner/utils/mcp.py` | upstream MCP gateway client | exposes only `tool_accepts_extra_headers` (+ its two pure helpers, copied verbatim) and `UnsupportedRequestFieldError`; the MCP session machinery is not vendored |
| `runner/utils/system_instructions.py` | upstream placement check | no-op `check_first_request()` (the upstream check context is never armed in Atoll) |
| `runner/agents/__init__.py`, `runner/agents/models.py` | upstream `runner.agents.models` | path shim re-exporting the message type aliases, `get_msg_role`/`get_msg_content`/`get_msg_attr` and `AgentConfigIds` (all copied verbatim); upstream's module also carries Studio report models not needed here |

## Reconciliation decisions

* `context_budget.py` / `context_windows.py` / `usage.py`: the upstream
  originals, vendored verbatim — the loop's imports (`DEFAULT_TRIM_FLOOR`, `PromptTokenProjector`,
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

## Grader file extraction (vendored verbatim)

Atoll's grader (`grading/atoll_grading`) renders files with upstream's own grader
extraction layer, from archipelago's **`grading/`** project at the same pinned commit.
Upstream agents and upstream grading both use the top-level package `runner`, so these
files live at their upstream import paths inside Atoll's `runner` package — no import
is rewritten. Their only cross-module import outside the layer, `runner.utils.decorators`
(the four `*_ctx` ContextVars used by the inert document cache), is satisfied by the
agents copy already vendored above. Reducto and Mercor's document cache are vendored
too and stay inert: they activate only with their API keys set, which Atoll never sets.

| Upstream path (in archipelago) | Local path (in atoll) | sha256 |
|---|---|---|
| `grading/runner/utils/tool_paths.py` | `agents/runner/utils/tool_paths.py` | `027c9567db2dcc5e6a5fc1a9378696b98c221b2b839847b987fa9f448c01e71b` |
| `grading/runner/utils/file_extraction/__init__.py` | `agents/runner/utils/file_extraction/__init__.py` | `e337d759bef7539ae4d68626d49eba7cd79e668560abe72344d7b694f7b9e9e7` |
| `grading/runner/utils/file_extraction/base.py` | `agents/runner/utils/file_extraction/base.py` | `01db3c08066287c6d6e8deeeef5ce6b679f4ea3db38932158e17fb93327f9ae0` |
| `grading/runner/utils/file_extraction/constants.py` | `agents/runner/utils/file_extraction/constants.py` | `e7ede38b118cca4fb571970d3deec8307f5f35e6a23a9cc0f5295b47ae717009` |
| `grading/runner/utils/file_extraction/types.py` | `agents/runner/utils/file_extraction/types.py` | `e61bd09de55ca4b476e62c8ff8161513f58f42fd1c1c37c253afb55eaa2f5f44` |
| `grading/runner/utils/file_extraction/factory.py` | `agents/runner/utils/file_extraction/factory.py` | `ff82f159d774d57f36011e6dff7cd2a135d25a719c21665848e795873bafffec` |
| `grading/runner/utils/file_extraction/methods/__init__.py` | `agents/runner/utils/file_extraction/methods/__init__.py` | `6637f42eadc415ac9c441208c5fb7ad8faf77195a6bfce56c51a90d68e1ec06a` |
| `grading/runner/utils/file_extraction/methods/archive_extractor.py` | `agents/runner/utils/file_extraction/methods/archive_extractor.py` | `fe94a9f925150dc904db23c35779fbee1d17b35a8a9cd7c449267a4117d42f03` |
| `grading/runner/utils/file_extraction/methods/local_extractor.py` | `agents/runner/utils/file_extraction/methods/local_extractor.py` | `5ce07d9d03b38dd3d6a2474e6246808a5d5529a95bd160fff53e6e2fd84b1bed` |
| `grading/runner/utils/file_extraction/methods/mercor_document_cache.py` | `agents/runner/utils/file_extraction/methods/mercor_document_cache.py` | `1be382b3591305aeabb4abb5cca2a23a08bad994ec2a816ae7904260d7c38eab` |
| `grading/runner/utils/file_extraction/methods/reducto_extractor.py` | `agents/runner/utils/file_extraction/methods/reducto_extractor.py` | `ba0437de4c73d065976870da2875110611df2d12ed91861b36dbb9267403d105` |
| `grading/runner/utils/file_extraction/methods/reducto/__init__.py` | `agents/runner/utils/file_extraction/methods/reducto/__init__.py` | `7bdbfa4c414d3d22366b7f24211d1af0e00d2f3538cc192edf62c7f7e7664e84` |
| `grading/runner/utils/file_extraction/methods/reducto/client.py` | `agents/runner/utils/file_extraction/methods/reducto/client.py` | `6234bb83e4ee186577330b9c4293c857d3ad9c9584b91400dc09f5d46f785d5f` |
| `grading/runner/utils/file_extraction/methods/reducto/types.py` | `agents/runner/utils/file_extraction/methods/reducto/types.py` | `4e5d27aa246ff3efd7f0270fe8aadf367793cb9bccb45a35fca4cca5d1ec7ad3` |
| `grading/runner/utils/file_extraction/utils/chart_extraction.py` | `agents/runner/utils/file_extraction/utils/chart_extraction.py` | `0be1966e2eccf90a82772913cc03cf278243f65b9e12ef2ceca9280d96a02424` |
| `grading/runner/utils/file_extraction/utils/docx_tracked_changes.py` | `agents/runner/utils/file_extraction/utils/docx_tracked_changes.py` | `b796856ef30e11e31ca3a955da833741b7b538079c44d10c686671d10301bc9a` |
| `grading/runner/utils/file_extraction/utils/pptx_speaker_notes.py` | `agents/runner/utils/file_extraction/utils/pptx_speaker_notes.py` | `54e1796e38d94c5658303a65fe8e148741e4a045b2ba12802c17189d747fdc47` |

### Runtime patches (grading only; no vendored file is edited)

`atoll_grading.render.apply_patches()` replaces three module attributes of
`runner.utils.file_extraction.methods.local_extractor` when the grader is imported
(nothing else imports the grader, so the agent loop never sees them):

| Attribute | Upstream behaviour | Atoll's replacement | Why |
|---|---|---|---|
| `evaluate_excel_formulas_with_libreoffice` | `soffice --convert-to xlsx`; LibreOffice keeps cached values; returns `None` on failure (the extractor then grades cached values under a banner) | `atoll_tools.office.recalculate`: same conversion with a throwaway profile forcing `OOXMLRecalcMode` = always; **raises** on failure | placeholder caches (xlsxwriter writes 0 for every formula) survived recalculation; a failed recalculation silently graded empty/stale cells |
| `_xlsx_has_uncached_formulas` | True iff some formula cell has no cached value | `atoll_tools.office.needs_recalculation`: also True when the workbook sets `fullCalcOnLoad` | xlsxwriter workbooks carry cached zeros, so upstream never recalculated them |
| `_is_numeric_display_format` | only currency / percent / `#,##0` formats reach the `Number Formats` block | upstream's check, widened to every non-General, non-date/time numeric format | precision requirements (`0.00`, `0`) are judged by the format |

The large-sheet summary (`【数据概要】`, sheets over 500 rows) is Atoll's own
post-processing of the extractor's output, not a patch.
