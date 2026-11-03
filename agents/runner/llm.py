# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Thin adapter over the vendored upstream LLM layer.

The real implementation is Archipelago's own ``runner/utils/llm.py``, vendored
byte-identical (see ``runner/utils/PATCHES.md`` for the pinned commit and the
file-by-file manifest). This module only re-exports it under the names the
loop, the tests and the e2e harness already use, and coerces the two
previously-optional arguments (``tools``/``extra_args`` may be None here).

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

from runner import atoll_fixes
from runner.utils import llm as _vendored
from runner.utils.decorators import with_retry
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
    enabled = atoll_fixes.fixes_enabled(extra_args)
    eff_args = atoll_fixes.strip_fixes_key(dict(extra_args or {}))
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
    return await _vendored.call_responses_api(
        model,
        messages,
        tools or [],
        llm_response_timeout,
        dict(extra_args or {}),
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
