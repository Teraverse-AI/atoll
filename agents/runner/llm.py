# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Thin adapter over the vendored upstream LLM layer.

The real implementation is Archipelago's own ``runner/utils/llm.py``, vendored
byte-identical (see ``runner/utils/PATCHES.md`` for the pinned commit and the
file-by-file manifest). This module only re-exports it under the names the
loop, the tests and the e2e harness use, and coerces two arguments upstream
requires (``tools``/``extra_args`` may be None here).

What this buys Atoll:

* **Full provider-specific request surgery, verbatim from upstream**, applied
  in BOTH routing modes: Anthropic image policy + empty-text placeholder
  filling + trailing-empty-prefill drop; Gemini trailing-user-turn
  normalization and tool-union preservation; Fireworks ``tool_choice``
  stripping; PDF inlining for providers that reject inline PDFs; non-Anthropic
  image size policies and remote-image inlining; Harmony token scrubbing and
  final-answer recovery; split Responses tool-choice merging; prompt-cache
  breakpoints (system prompt, last tool, rolling last message) for allowlisted
  providers; ``responses_args_to_completions`` extra-args reshaping.
* **Target resolution via environment**: with ``LITELLM_BASE_URL`` /
  ``LITELLM_API_KEY`` set, calls go to the configured OpenAI-compatible
  endpoint (e.g. a LiteLLM proxy) exactly as upstream's proxy branch sends
  them; with them unset, kwargs are left untouched and litellm routes DIRECT
  to the provider (provider-prefixed model + provider API keys from env).
* **Per-model settings** (``runner/model_config.yaml``, optional): a model name
  maps to the provider model id, credentials, headers and request settings,
  like a proxy's model_list (see ``runner/model_config.py``).
* **Spend tags**: calls routed to the endpoint also carry
  ``x-atoll-task`` / ``x-atoll-trajectory`` headers (a LiteLLM proxy can
  harvest them via ``litellm_settings.extra_spend_tag_headers``), next to
  upstream's own spend-tag headers.
* **Upstream retry semantics** (``runner/utils/decorators.with_retry``):
  same retry_on/skip_on/skip_if sets, backoff and per-call contextvars.

Infra buckets are vendored-but-disabled via same-path shims, not subtracted:
metrics are no-ops, the Redis spend guardrail sees no client, CAS ledger emit
soft-disables without its client package, streaming stays available but off by
default (``stream=False``; it still self-enables when a Responses-routed model
asks for encrypted reasoning, as upstream does).

Divergence kept: ``is_system_error`` (and only it) remains Atoll's ported
classifier below — upstream's lives in ``runner/utils/error.py``, which pulls
the MCP client package for gateway-session classification Atoll doesn't run.
The ported copy keeps upstream's exception-class dispatch and message
heuristics and reuses the vendored ``_is_non_retriable_bad_request`` list.
"""

from typing import Any

import litellm
from litellm.exceptions import (
    APIConnectionError,
    BadRequestError,
    ContextWindowExceededError,
    InternalServerError,
    RateLimitError,
    ServiceUnavailableError,
    Timeout,
)
from loguru import logger

from runner import atoll_fixes, model_config
from runner.utils import llm as _vendored
from runner.utils.decorators import task_id_ctx, with_retry
from runner.utils.llm import (
    _drop_unrequested_encrypted_reasoning,
    _is_context_window_error,
    _is_non_retriable_bad_request,
    _model_rates,
    _should_skip_retry,
    asks_for_encrypted_reasoning,
    compute_call_cost_usd,
    encrypted_reasoning_items,
    is_responses_api_model,
    reasoning_replay_items,
)
from runner.utils.settings import get_settings

# The vendored module turns this global on when an endpoint is configured; Atoll
# routes per call instead (settings.apply_llm_target), so direct models can bypass it.
litellm.use_litellm_proxy = False

__all__ = [
    "asks_for_encrypted_reasoning",
    "call_responses_api",
    "compute_call_cost_usd",
    "encrypted_reasoning_items",
    "generate_response",
    "is_responses_api_model",
    "is_system_error",
    "reasoning_replay_items",
    "with_retry",
    "_drop_unrequested_encrypted_reasoning",
    "_is_context_window_error",
    "_is_non_retriable_bad_request",
    "_model_rates",
    "_should_skip_retry",
]


def _with_endpoint_headers(
    extra_args: dict[str, Any], model: str, trajectory_id: str | None
) -> dict[str, Any]:
    """Spend-tag headers for calls routed to the configured endpoint
    (``LITELLM_BASE_URL``); none on direct calls. ``x-atoll-task`` /
    ``x-atoll-trajectory`` — a LiteLLM proxy can harvest them
    (``litellm_settings.extra_spend_tag_headers``). Caller-set headers win.
    """
    if not get_settings().routes_via_endpoint(model):
        return extra_args
    headers = {}
    if task_id := task_id_ctx.get():
        headers["x-atoll-task"] = task_id
    if trajectory_id:
        headers["x-atoll-trajectory"] = trajectory_id
    if not headers:
        return extra_args
    return {**extra_args, "extra_headers": {**headers, **(extra_args.get("extra_headers") or {})}}


def _direct_model_settings(
    model: str, extra_args: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    """Per-model settings from model_config.yaml (whole entry on direct calls,
    extra_headers only on endpoint calls)."""
    direct = not get_settings().routes_via_endpoint(model)
    return model_config.resolve(model, dict(extra_args or {}), direct=direct)


async def generate_response(
    model: str,
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    llm_response_timeout: int,
    extra_args: dict[str, Any] | None,
    trajectory_id: str | None = None,
    **kwargs: Any,
) -> Any:
    """Vendored ``generate_response`` with Atoll's optional-argument contract.

    ``kwargs`` passes through upstream's extra parameters (``stream``,
    ``mark_last_message``, ``max_attempts``, ``system_instructions_harness``),
    which keep their upstream defaults when omitted.

    Atoll-local provider fixes (``runner/atoll_fixes.py``) are applied around
    the vendored call for providers upstream never covered — currently ARK
    thinking models (request shaping + empty-thinking-reply retries). Disable
    per call with ``extra_args={"atoll_fixes": false}``.
    """
    model, extra_args = _direct_model_settings(model, extra_args)
    enabled = atoll_fixes.fixes_enabled(extra_args)
    eff_args = atoll_fixes.strip_fixes_key(dict(extra_args or {}))
    eff_args = _with_endpoint_headers(eff_args, model, trajectory_id)
    if enabled:
        llm_response_timeout, eff_args = atoll_fixes.shape_request(
            model, llm_response_timeout, eff_args
        )

    response = await _vendored.generate_response(
        model,
        messages,
        tools or [],
        llm_response_timeout,
        dict(eff_args),
        trajectory_id=trajectory_id,
        **kwargs,
    )
    if not enabled:
        return response

    for attempt in range(1, atoll_fixes.MAX_EMPTY_THINKING_RETRIES + 1):
        if not atoll_fixes.is_empty_thinking_reply(model, response):
            break
        eff_args = atoll_fixes.retry_args(eff_args)
        logger.info(
            "atoll_fixes: empty thinking reply from {}; retry {}/{} with max_tokens={}",
            model,
            attempt,
            atoll_fixes.MAX_EMPTY_THINKING_RETRIES,
            eff_args.get("max_tokens"),
        )
        response = await _vendored.generate_response(
            model,
            messages,
            tools or [],
            llm_response_timeout,
            dict(eff_args),
            trajectory_id=trajectory_id,
            **kwargs,
        )
    return response


async def call_responses_api(
    model: str,
    messages: list[Any],
    tools: list[dict[str, Any]] | None,
    llm_response_timeout: int,
    extra_args: dict[str, Any] | None,
    trajectory_id: str | None = None,
    keep_encrypted_reasoning: bool = False,
    **kwargs: Any,
) -> Any:
    """Vendored ``call_responses_api`` with Atoll's optional-argument contract."""
    model, extra_args = _direct_model_settings(model, extra_args)
    return await _vendored.call_responses_api(
        model,
        messages,
        tools or [],
        llm_response_timeout,
        _with_endpoint_headers(dict(extra_args or {}), model, trajectory_id),
        trajectory_id=trajectory_id,
        keep_encrypted_reasoning=keep_encrypted_reasoning,
        **kwargs,
    )


def is_system_error(exception: Exception) -> bool:
    """Determine if an exception represents a system error vs model error.

    System errors are transient infrastructure issues. Model errors are
    non-retryable failures like context overflow.

    Returns:
        True if the exception is a system error (should use ERROR status),
        False if it's a model error (should use FAILED status).
    """
    if isinstance(
        exception,
        (
            RateLimitError,
            Timeout,
            ServiceUnavailableError,
            APIConnectionError,
            InternalServerError,
        ),
    ):
        return True

    # BadRequestError could be either, check the error message
    if isinstance(exception, BadRequestError):
        error_str = str(exception).lower()
        if "exceeded your current quota" in error_str:
            return True  # System error
        # If it's context/token/multimodal related, it's a model error
        if (
            "context" in error_str
            or "token" in error_str
            or "is not a multimodal model" in error_str
            or "does not support multimodal" in error_str
        ):
            return False  # Model error
        # Deterministic request-validation / policy refusals are model errors.
        if _is_non_retriable_bad_request(exception):
            return False
        return True  # System error (configuration/infrastructure issue)

    # Model errors (non-retryable)
    if isinstance(exception, ContextWindowExceededError):
        return False

    # ValueError is typically a configuration/validation error (non-retryable)
    if isinstance(exception, ValueError):
        return False

    # RuntimeError (e.g. SSE error events) — non-retryable
    if isinstance(exception, RuntimeError):
        return False

    # Unknown exceptions default to system error (safer than blaming the model)
    return True
