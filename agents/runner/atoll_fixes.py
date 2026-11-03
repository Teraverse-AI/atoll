"""Atoll-local provider fixes, layered ON TOP of the vendored upstream surgery.

The vendored ``runner/utils/llm.py`` carries Mercor's request repair for the
providers Mercor used (Anthropic, Gemini/Vertex, Fireworks, OpenAI). Providers
they never called get their fixes HERE — never by editing vendored bytes — so
upstream resyncs stay clean. The adapter in ``runner/llm.py`` applies this
module before/around the vendored call; models it doesn't recognize pass
through untouched. Per-call escape hatch: ``extra_args={"atoll_fixes": false}``
(the key is always stripped before the wire either way).

Current fixes
=============

**Volcengine ARK thinking models (DeepSeek V4 family)** — measured against
``ark.cn-beijing.volces.com`` (2026-08-27), not inferred from docs:

* Thinking tokens are drawn from the SAME ``max_tokens`` budget as the answer,
  and thinking is on by default. When reasoning exhausts the budget, the reply
  is well-formed but empty: ``content: ""`` beside a large
  ``reasoning_content``, ``finish_reason`` ``"length"`` or even ``""``.
  → a caller-set ``max_tokens`` below 16384 is raised to 16384 (unless the
  caller disabled thinking); an absent ``max_tokens`` is left absent.
* Answers routinely take 2–8 minutes of silent thinking.
  → the per-call LLM timeout is raised to at least 900 s.
* An empty-content/thinking-exhausted reply must be treated as retryable,
  never as the model's answer (the loop would otherwise finalize "No content"
  and the run would grade as a failure).
  → retried up to twice, DOUBLING the token budget each time. The model's
  configuration is never changed mid-run (no thinking-disabled fallback —
  dropped 2026-11-03): every scored answer stays attributable to one setup,
  which is what makes benchmark results comparable.

Retried attempts' token spend is not added to the run's UsageTracker (only the
returned response is tracked by the loop) — same characteristic as upstream's
``with_retry`` on failed attempts.
"""

from typing import Any

ARK_PREFIXES = ("volcengine/", "volcengineC/")
# Model-name markers of ARK models with the shared thinking/max_tokens budget.
# Extend as measured (the GLM plan variants show the same family behavior).
ARK_THINKING_MARKERS = ("deepseek", "glm")

MIN_THINKING_MAX_TOKENS = 16_384
DEFAULT_RETRY_MAX_TOKENS = 32_768
MIN_TIMEOUT_SECONDS = 900
MAX_EMPTY_THINKING_RETRIES = 2

FIXES_KEY = "atoll_fixes"


def fixes_enabled(extra_args: dict[str, Any] | None) -> bool:
    """Whether the per-call escape hatch left fixes on (default: on)."""
    return bool((extra_args or {}).get(FIXES_KEY, True))


def strip_fixes_key(extra_args: dict[str, Any]) -> dict[str, Any]:
    """``extra_args`` without the escape-hatch key (it must never hit the wire)."""
    return {k: v for k, v in extra_args.items() if k != FIXES_KEY}


def is_ark_thinking_model(model: str) -> bool:
    """Whether ``model`` is an ARK route with the shared thinking budget quirk."""
    if not model.startswith(ARK_PREFIXES):
        return False
    bare = model.split("/", 1)[1].lower()
    return any(marker in bare for marker in ARK_THINKING_MARKERS)


def _thinking_disabled(extra_args: dict[str, Any]) -> bool:
    thinking = extra_args.get("thinking")
    return isinstance(thinking, dict) and thinking.get("type") == "disabled"


def shape_request(
    model: str, llm_response_timeout: int, extra_args: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """The (timeout, extra_args) to actually send for ``model``.

    No-op for models without a fix. Never lowers a caller's values.
    """
    if not is_ark_thinking_model(model):
        return llm_response_timeout, extra_args
    shaped = dict(extra_args)
    if not _thinking_disabled(shaped):
        max_tokens = shaped.get("max_tokens")
        if isinstance(max_tokens, int) and max_tokens < MIN_THINKING_MAX_TOKENS:
            shaped["max_tokens"] = MIN_THINKING_MAX_TOKENS
    return max(llm_response_timeout, MIN_TIMEOUT_SECONDS), shaped


def _message_of(response: Any) -> Any:
    try:
        choices = getattr(response, "choices", None) or response["choices"]
        choice = choices[0]
        return getattr(choice, "message", None) or choice["message"]
    except (TypeError, KeyError, IndexError, AttributeError):
        return None


def _attr(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def is_empty_thinking_reply(model: str, response: Any) -> bool:
    """A well-formed reply whose answer was eaten by thinking: retryable.

    True only for affected models, when content is empty, no tool calls were
    made, and reasoning_content shows the thinking that consumed the budget.
    """
    if not is_ark_thinking_model(model):
        return False
    message = _message_of(response)
    if message is None:
        return False
    if _attr(message, "tool_calls"):
        return False
    content = _attr(message, "content")
    reasoning = _attr(message, "reasoning_content")
    return (not content) and bool(reasoning)


def retry_args(extra_args: dict[str, Any]) -> dict[str, Any]:
    """extra_args for the next retry after an empty-thinking reply.

    Only the token budget escalates (doubled, or ``DEFAULT_RETRY_MAX_TOKENS``
    when absent). The configuration — thinking on, effort — is never touched,
    so a retried answer comes from the same setup as a first-try answer.
    """
    retried = dict(extra_args)
    current = retried.get("max_tokens")
    retried["max_tokens"] = (
        current * 2 if isinstance(current, int) else DEFAULT_RETRY_MAX_TOKENS
    )
    return retried
