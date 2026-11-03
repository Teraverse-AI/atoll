# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Loop Truncated Tools Agent implementation.

Ported from Archipelago's runner/agents/loop_truncated_tools_agent/main.py
(plus the ``NoToolCallsTurnMixin`` from runner/agents/loop_agent/main.py).
This is a variant of the loop agent that truncates tool outputs to prevent
context window overflow from large tool responses.

Divergences from upstream (model-visible behavior otherwise matches the
chat-completions path):

* **Tools come from a ToolProvider, not an MCP gateway.** ``list_tools()`` is
  read once at startup; ``call_tool(name, arguments)`` runs in a thread under
  ``tool_call_timeout``. The MCP machinery is dropped with it: gateway schema
  construction, session acquisition/health probes, ``is_fatal_mcp_error``
  (a provider exception is reported to the model and the run continues),
  shielded-task draining, and ``content_blocks_to_messages`` — observations
  map as: str → tool-message text verbatim; dict/pydantic-dump →
  ``json.dumps``; an object with ``mime_type``+``base64_data`` → the tool
  message "Image result attached in the following message." plus a deferred
  user message carrying the image data URI (appended after the batch, like
  upstream's deferred image messages). Malformed tool-call JSON arguments
  become an "Error executing tool {name}: {exc}" observation without reaching
  the tool (the gateway's error text).
* **The Responses API path is ported** with upstream's gating: a
  ``responses/`` segment in the orchestrator model selects
  ``_get_responses_reply`` unless ``litellm_routes_responses`` is set, in
  which case the prefixed name is sent whole over Chat Completions and the
  configured OpenAI-compatible endpoint (e.g. a LiteLLM proxy) resolves it to
  the Responses API server-side. Message/tool conversion, output parsing and
  reasoning-summary extraction live in ``runner.responses``;
  ``replay_reasoning`` (default False, coerced) keeps encrypted reasoning
  items on the stored assistant message for replay, and the truncated agent's
  ``_turn_carried_reasoning`` override (replayed encrypted reasoning counts
  as a reasoning turn) is restored. ``responses_fn`` is injectable alongside
  ``llm_fn``; the stored assistant message is a plain dict on this path too.
* **Messages are plain dicts.** The assistant reply is normalized to an
  OpenAI-shaped dict on receipt (upstream keeps litellm ``Message`` pydantic
  objects); upstream's ``isinstance(choices[0], Choices)`` check is replaced
  by duck-typed extraction so an injected scripted LLM works.
* **``is_known_text_only`` image stripping is dropped** (it pulls a model
  table); tool-result images always ride in the deferred user message.
* **No Datadog / S3 in the loop.** The loop logs via plain ``logging`` (the
  vendored LLM layer underneath logs through loguru, as upstream); the
  terminal error and fault that upstream captures via a loguru sink are set
  directly on the output, and the final answer is recorded by
  ``_finalize_turn`` instead of a ``final_answer`` log sink.
* **Provider-specific request surgery is PRESENT**, vendored verbatim from
  upstream at a pinned commit (see ``runner/utils/PATCHES.md``):
  ``runner.llm.generate_response`` is upstream's own pre-call pipeline
  (Anthropic/Gemini/Fireworks request normalization, image/PDF policies,
  Harmony recovery, prompt-cache breakpoints), applied identically whether
  requests go to the configured OpenAI-compatible endpoint (e.g. a LiteLLM
  proxy) or direct to the provider. The upstream infra buckets (Datadog
  metrics, Redis spend guardrail, CAS ledger, streaming telemetry) are
  vendored-but-disabled via same-path shims, not subtracted.
* **Spill writes locally.** ``spill_to_filesystem`` (HTTP upload to the
  sandbox) is replaced by ``spill_to_workspace`` (direct write into the
  per-run workspace); the spill notice text and safe-name construction are
  upstream's, and the notice's path is the '/'-rooted virtual path tools
  accept.
* ``disabled_tools`` filters the provider's list and still refuses a call to
  a disabled tool, as upstream.
* ``llm_fn`` / ``responses_fn`` are injectable (sync or async); the defaults
  are ``runner.llm.generate_response`` / ``runner.llm.call_responses_api``,
  resolved at construction so tests and the e2e harness can substitute a
  scripted fake.
"""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from litellm.exceptions import ContextWindowExceededError, Timeout

from runner.llm import (
    _is_context_window_error,
    asks_for_encrypted_reasoning,
    compute_call_cost_usd,
    is_system_error,
)
from runner.models import (
    AgentRunInput,
    AgentStatus,
    AgentTrajectoryOutput,
    Budget,
    budget_exhausted,
    content_to_str,
    empty_response,
)
from runner.providers.base import ToolProvider
from runner.responses import (
    convert_messages_for_responses_api,
    extract_reasoning_summary,
    parse_responses_api_output,
    responses_output_to_message,
    to_responses_tool,
)
from runner.spill import spill_to_workspace
from runner.utils.context_budget import (
    DEFAULT_TRIM_FLOOR,
    PromptTokenProjector,
    count_tokens,
    resolve_max_input_tokens,
    trim_to_budget,
)
from runner.utils.usage import UsageTracker

logger = logging.getLogger(__name__)

# Consecutive unusable replies before the run is abandoned instead of nudged.
MAX_CONSECUTIVE_EMPTY_REPLIES = 4

# Local twin of MAX_CONSECUTIVE_EMPTY_REPLIES, for reasoning-only turns.
MAX_CONSECUTIVE_REASONING_ONLY_TURNS = 4

# Does not refer the model back to its own reasoning: the request is rebuilt
# from `self.messages`, which carry none.
REASONING_ONLY_NUDGE = (
    "Your last turn gave no answer and made no tool call. Do not send "
    "another turn like that. Either call a tool to do the work, or state "
    "your final answer as text. Do not restate your reasoning as the "
    "answer: your working is not the deliverable. Give the result the task "
    "asked for, or take the action that produces it."
)

# Injected each step so the model wraps up before it runs out of steps.
# This loop finalizes on a response with no tool calls, so it steers toward
# "provide your final answer" rather than a termination tool.
TURN_WARNING_TEMPLATE = (
    "Warning: {remaining} step(s) remaining before this run ends. "
    "Provide your final answer before running out of steps."
)

# Joined with the turn warning (space-separated) when a budget is configured;
# stands alone when turn warnings are off.
TOKEN_BUDGET_WARNING_TEMPLATE = (
    "You have {tokens_remaining} of {token_budget} token(s) remaining "
    "in your total token budget."
)

# Injected instead of the turn warning once the token budget is spent.
TOKEN_BUDGET_EXHAUSTED_TEMPLATE = (
    "Warning: your token budget of {token_budget} token(s) is exhausted "
    "({tokens_spent} token(s) spent). This is your final turn. "
    "Provide your final answer now."
)

# Injected each step in "cost_accounting" mode, once cost_budget_usd is set.
COST_BUDGET_WARNING_TEMPLATE = (
    "You have ${cost_remaining:.4f} of ${cost_budget:.4f} remaining "
    "in your total cost budget."
)

# Injected instead of the cost-budget warning once the cost budget is spent.
COST_BUDGET_EXHAUSTED_TEMPLATE = (
    "Warning: your cost budget of ${cost_budget:.4f} is exhausted "
    "(${cost_spent:.4f} spent). This is your final turn. "
    "Provide your final answer now."
)

# Appended as a user message immediately after a context trim fires, so a
# degraded answer is explainable and the model knows to re-fetch rather than
# totalling a view it can no longer see. Silent tail-drop is the failure mode
# where an agent answers from a partial history believing it complete.
CONTEXT_TRIM_NOTICE_TEMPLATE = (
    "Note: {dropped_turns} earlier exchange(s) ({dropped_messages} message(s), "
    "including their tool results) were removed from this conversation to keep "
    "it within the model's input limit. That content is no longer visible to "
    "you. Do not treat anything you can no longer see as complete — if you "
    "need that data, fetch it again, and prefer narrower queries from here on."
)

# Substituted for a tool result that would blow this step's tool-result
# budget. Deliberately a refusal with a preview rather than a quietly
# shortened payload: an agent handed a silently truncated result totals it
# and reports the total as if it were complete.
STEP_TOOL_BUDGET_REFUSAL_TEMPLATE = (
    "Error: this result was NOT delivered. It is ~{tokens} token(s) "
    "({chars} characters) and would exceed this step's tool-result budget "
    "({remaining} of {budget} token(s) left; the earlier calls in this "
    "parallel batch used the rest). None of it is in your context — the "
    "preview below is not partial data, do not total it or draw conclusions "
    "from it. Re-run this call with a narrower query, a smaller page size, "
    "or fewer records, and consider making fewer calls per turn."
    "\n\nFirst {preview_chars} characters of the discarded result:\n{preview}"
)

# Substituted for an over-budget tool result that was spilled to the sandbox
# filesystem rather than refused. Says "complete" explicitly: the refusal it
# replaces spends its wording warning the model not to treat what it can see
# as whole data, and that warning would be exactly wrong here.
STEP_TOOL_BUDGET_SPILL_TEMPLATE = (
    "This result was too large to deliver inline (~{tokens} token(s), "
    "{chars} characters, against {remaining} of {budget} token(s) left in this "
    "step's tool-result budget), so it was written to the environment "
    "filesystem instead of being discarded.\n\n"
    "The COMPLETE result is at: {path}\n\n"
    "It is whole, not truncated. Read or aggregate it with the code execution "
    "tool — that tool shares this filesystem — rather than re-fetching it "
    "through a tool call, which would hit the same budget again."
    "\n\nFirst {preview_chars} characters, so you can see what it is:\n{preview}"
)

# How much of a refused result to show back, purely so the model can tell what
# it asked for. Small enough that a whole batch of refusals cannot itself
# become the overflow.
_REFUSAL_PREVIEW_CHARS = 400

# Accounting modes: "default" (no budget/warning mechanism active), "token_accounting"
# (the existing token_budget/turn_warnings_enabled mechanism), "cost_accounting" (real
# $ cost tracking via rate overrides + cost_budget_usd). Mutually exclusive.
_ACCOUNTING_MODES = frozenset({"default", "token_accounting", "cost_accounting"})


def _coerce_bool(value: Any, *, default: bool) -> bool:
    """Capability-gate-safe bool coercion for agent config values.

    Recognizes explicit intent only: bool True/False, "true"/"false"
    (case/whitespace-insensitive), and "0"/int 0 as False. Anything else —
    missing, None, garbage — resolves to ``default``, so a typo can never
    silently flip a capability away from its documented default.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered in ("false", "0"):
            return False
        return default
    if isinstance(value, int) and value == 0:
        return False
    return default


def _message_text(msg: Any) -> str:
    """The text of a tool-result message, whether stored flat or as blocks."""
    if not isinstance(msg, dict):
        return ""
    content: Any = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _spillable_text(msg: Any) -> str | None:
    """The full text of ``msg``, or None when it cannot be spilled faithfully.

    A spill tells the model the file on disk is the complete result, so it is
    only offered when the bytes to write are unambiguous:

    * a plain string content — the whole result, exactly;
    * a single text block — likewise.

    Anything else keeps the refusal. A mixed text+image result would lose its
    image blocks in the write, and several text blocks have no defined
    separator between them: joining with a space puts spaces inside base64
    (which then will not decode) and joining with nothing silently welds
    genuinely separate texts together. Neither is safe under a notice that
    calls the file complete.
    """
    if not isinstance(msg, dict):
        return None
    content: Any = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[Any] = list(content)
        if len(parts) != 1:
            return None
        part = parts[0]
        if not isinstance(part, dict) or part.get("type") != "text":
            return None
        return str(part.get("text", ""))
    return None


def _coerce_choice(value: Any, *, choices: frozenset[str], default: str) -> str:
    """Allowlist coercion for a config value restricted to a fixed set of strings.

    Same defensive philosophy as `_coerce_bool`: missing/None/garbage/typo'd
    values all resolve to `default`, never passed through raw.
    """
    if isinstance(value, str) and value.strip().lower() in choices:
        return value.strip().lower()
    return default


def _coerce_fraction(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    """Clamped float coercion for a config value expressed as a fraction.

    Same defensive philosophy as `_coerce_bool`: missing/None/garbage all
    resolve to `default`, and an out-of-range number is clamped rather than
    honoured — a typo'd utilization must never trim the whole history away.
    """
    try:
        coerced = float(value)
    except (TypeError, ValueError):
        return default
    if coerced != coerced:  # NaN
        return default
    return min(max(coerced, minimum), maximum)


def _coerce_positive_int(value: Any, *, default: int) -> int:
    """`value` as an int >= 1, else `default` (bools, None, garbage, < 1)."""
    if isinstance(value, bool):
        return default
    try:
        coerced = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return coerced if coerced >= 1 else default


def _truncate_output(content: str, max_lines: int = 200, max_chars: int = 32768) -> str:
    """Truncate content at whichever limit is hit first: max_lines or max_chars."""
    if not content:
        return content

    lines = content.split("\n")
    total_lines = len(lines)

    # Check if we need to truncate by lines
    if total_lines > max_lines:
        truncated_content = "\n".join(lines[:max_lines])
        # Also check char limit on the line-truncated content
        if len(truncated_content) > max_chars:
            truncated_content = truncated_content[:max_chars]
            kept_lines = truncated_content.count("\n") + 1
            hidden_lines = total_lines - kept_lines
        else:
            hidden_lines = total_lines - max_lines
        return truncated_content + f"\n... (truncated {hidden_lines} lines) ..."

    # Check if we need to truncate by chars
    if len(content) > max_chars:
        truncated_content = content[:max_chars]
        kept_lines = truncated_content.count("\n") + 1
        hidden_lines = total_lines - kept_lines

        if hidden_lines > 0:
            suffix = f"\n... (truncated {hidden_lines} lines) ..."
        else:
            hidden_chars = len(content) - len(truncated_content)
            suffix = f"\n... (truncated {hidden_chars} chars) ..."

        return truncated_content + suffix

    return content


def _normalize_tool_call(tc: Any) -> dict[str, Any]:
    """One tool call as a plain OpenAI-shaped dict, from a dict or an object."""
    if isinstance(tc, dict):
        fn = tc.get("function") or {}
        name = fn.get("name") if isinstance(fn, dict) else getattr(fn, "name", None)
        args = fn.get("arguments") if isinstance(fn, dict) else getattr(fn, "arguments", None)
        return {
            "id": tc.get("id"),
            "type": tc.get("type") or "function",
            "function": {"name": name, "arguments": args},
        }
    fn = getattr(tc, "function", None)
    return {
        "id": getattr(tc, "id", None),
        "type": getattr(tc, "type", None) or "function",
        "function": {
            "name": getattr(fn, "name", None),
            "arguments": getattr(fn, "arguments", None),
        },
    }


def _normalize_assistant_message(message: Any) -> dict[str, Any]:
    """The assistant reply as a plain OpenAI-shaped dict.

    Accepts a litellm ``Message`` (pydantic), a dict, or any object exposing
    the usual attributes (so a scripted fake works). Reasoning fields are kept
    when present — upstream replays them to the provider as part of the stored
    message.
    """
    if isinstance(message, dict):
        raw: dict[str, Any] = dict(message)
    elif hasattr(message, "model_dump"):
        raw = message.model_dump()
    else:
        raw = {
            key: getattr(message, key, None)
            for key in (
                "role",
                "content",
                "tool_calls",
                "reasoning_content",
                "thinking_blocks",
                "reasoning_items",
            )
        }
    out: dict[str, Any] = {"role": raw.get("role") or "assistant", "content": raw.get("content")}
    tool_calls = raw.get("tool_calls")
    if tool_calls:
        out["tool_calls"] = [_normalize_tool_call(tc) for tc in tool_calls]
    # reasoning_items: encrypted Responses-API reasoning a routed chat reply can
    # carry; kept so replay (and the reasoning-turn check) sees it.
    for key in ("reasoning_content", "thinking_blocks", "reasoning_items"):
        if raw.get(key) is not None:
            out[key] = raw[key]
    return out


class NoToolCallsTurnMixin:
    """Shared handling for a model turn that comes back with no tool calls.

    Ported from Archipelago's loop_agent. The host class declares and
    initializes the attributes below in its own `__init__`.
    """

    model: str
    messages: list[Any]
    _usage_tracker: UsageTracker
    _finalized: bool
    _final_answer: str | None
    _abort_reason: str | None
    _consecutive_reasoning_only_turns: int

    @staticmethod
    def _turn_carried_reasoning(response_message: dict[str, Any]) -> bool:
        """Report whether the turn carried reasoning or thinking text."""
        reasoning = response_message.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning.strip():
            return True
        blocks = response_message.get("thinking_blocks")
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                thinking = block.get("thinking")
                if isinstance(thinking, str) and thinking.strip():
                    return True
                # Anthropic's safety-redacted reasoning: opaque `data`, no
                # `thinking`. Proves a thinking turn; payload is never read.
                if block.get("type") == "redacted_thinking" and block.get("data"):
                    return True
        return False

    def _handle_no_tool_calls(self, response_message: dict[str, Any]) -> None:
        """End the run on a tool-less turn, unless the turn was reasoning only.

        A turn with no tool calls and no content, but with reasoning or
        thinking text, is a model that worked something out and then neither
        spoke nor acted. Finalizing it records the placeholder "No content" as
        the run's answer, which grades as a bad answer instead of surfacing as
        a harness failure. So nudge the model and let the loop continue.

        The reasoning text itself is never promoted into the answer. It holds
        abandoned branches and self-corrections, so handing it to a grader is
        worse than failing the run.

        After MAX_CONSECUTIVE_REASONING_ONLY_TURNS the loop stops and `run()`
        records a terminal error. Any turn with content or tool calls resets
        the counter, and every such turn keeps its existing behaviour.
        """
        content = response_message.get("content")
        if not content and self._turn_carried_reasoning(response_message):
            self._consecutive_reasoning_only_turns += 1
            seen = self._consecutive_reasoning_only_turns
            if seen >= MAX_CONSECUTIVE_REASONING_ONLY_TURNS:
                self._abort_reason = (
                    f"{self.model} produced reasoning with no answer and no "
                    f"tool call in {seen} consecutive steps"
                )
                logger.warning(
                    f"Reasoning-only turn ({seen}/"
                    f"{MAX_CONSECUTIVE_REASONING_ONLY_TURNS}), giving up"
                )
                return
            logger.warning(
                f"Reasoning-only turn ({seen}/"
                f"{MAX_CONSECUTIVE_REASONING_ONLY_TURNS}), asking the model "
                f"to act or answer"
            )
            self.messages.append({"role": "user", "content": REASONING_ONLY_NUDGE})
            return

        self._finalize_turn(content)

    def _finalize_turn(self, content: Any) -> None:
        """End the run on this turn, with `content` as the final answer."""
        self._consecutive_reasoning_only_turns = 0
        self._finalized = True
        self._usage_tracker.track_final_answer(content)
        self._final_answer = content_to_str(content) if content else "No content"
        logger.info(f"Final answer: {self._final_answer}")


class LoopTruncatedToolsAgent(NoToolCallsTurnMixin):
    """
    A loop-based agent that truncates tool outputs to prevent context overflow.

    Same as LoopAgent but applies _truncate_output() to all tool responses.
    """

    @staticmethod
    def _turn_carried_reasoning(response_message: dict[str, Any]) -> bool:
        """Also count replayed encrypted reasoning, which has no text."""
        return bool(
            response_message.get("reasoning_items")
        ) or NoToolCallsTurnMixin._turn_carried_reasoning(response_message)

    def __init__(
        self,
        run_input: AgentRunInput,
        provider: ToolProvider,
        llm_fn: Callable[..., Any] | None = None,
        responses_fn: Callable[..., Any] | None = None,
    ):
        self.trajectory_id: str = run_input.trajectory_id
        self.model: str = run_input.orchestrator_model
        self.messages: list[Any] = list(run_input.initial_messages)
        self.provider: ToolProvider = provider

        if not run_input.workspace_dir:
            raise ValueError("workspace_dir is required for loop truncated tools agent")
        self._workspace_dir: str = run_input.workspace_dir

        if llm_fn is None or responses_fn is None:
            from runner import llm as _llm

            if llm_fn is None:
                llm_fn = _llm.generate_response
            if responses_fn is None:
                responses_fn = _llm.call_responses_api
        self._llm_fn: Callable[..., Any] = llm_fn
        self._responses_fn: Callable[..., Any] = responses_fn

        self._finalized: bool = False
        self._final_answer: str | None = None
        self._terminal_error: str | None = None
        self._terminal_fault: dict[str, Any] | None = None
        self._consecutive_empty_replies: int = 0
        self._empty_reply_had_output: bool = False
        self._abort_reason: str | None = None
        self._consecutive_reasoning_only_turns: int = 0
        self.tools: list[dict[str, Any]] = []

        # Agent config values (with defaults)
        config = run_input.agent_config_values
        self.tool_call_timeout: int = config.get("tool_call_timeout", 60)
        self.llm_response_timeout: int = config.get("llm_response_timeout", 600)
        self.max_steps: int = config.get("max_steps", 100)
        self.timeout: int = config.get("timeout", 10800)  # 3 hours
        self.replay_reasoning: bool = _coerce_bool(config.get("replay_reasoning"), default=False)

        # Coerced: both caps slice every tool result, so null/garbage must not reach it.
        self.max_output_lines: int = _coerce_positive_int(
            config.get("max_output_lines"), default=200
        )
        self.max_output_chars: int = _coerce_positive_int(
            config.get("max_output_chars"), default=32768
        )

        # Total provider-reported prompt+completion tokens the run may spend.
        # 0 disables budgeting. Defensively coerced: agent_config_values is a
        # passthrough dict, so missing/null/malformed values all resolve to
        # "disabled".
        try:
            raw_token_budget = max(int(config.get("token_budget") or 0), 0)
        except (TypeError, ValueError):
            raw_token_budget = 0
        # Inject a per-step "N step(s) remaining" turn warning. Off by default
        # so ordinary runs are unaffected; independent of token_budget.
        raw_turn_warnings_enabled = _coerce_bool(
            config.get("turn_warnings_enabled"), default=False
        )

        # accounting_mode picks one of three mutually-exclusive families: the
        # existing pure-token mechanism (token_budget) or the real $ cost
        # mechanism below. Unrecognized/missing values default to "default"
        # (no mechanism active). Back-compat: agents configured before this
        # field existed already have token_budget set directly with no
        # accounting_mode — promote those to "token_accounting" so their
        # warnings keep firing unchanged. Keyed on the RAW value being None
        # (the key truly absent), not the coerced value equaling "default" —
        # a user who explicitly switched back to "default" with a stale
        # token_budget still in the config must have that choice stick
        # instead of being silently re-promoted.
        raw_accounting_mode = config.get("accounting_mode")
        accounting_mode = _coerce_choice(
            raw_accounting_mode, choices=_ACCOUNTING_MODES, default="default"
        )
        if raw_accounting_mode is None and raw_token_budget:
            accounting_mode = "token_accounting"
        self.accounting_mode: str = accounting_mode
        self.token_accounting_active: bool = accounting_mode == "token_accounting"
        self.cost_accounting_active: bool = accounting_mode == "cost_accounting"

        self.token_budget: int = raw_token_budget if self.token_accounting_active else 0
        # Orthogonal to accounting_mode: a step-count reminder, unrelated to
        # which budget currency (if any) is being tracked, so it applies in
        # every mode — unlike token_budget, which is specific to
        # token_accounting.
        self.turn_warnings_enabled: bool = raw_turn_warnings_enabled

        # Per-token $ rate overrides for cost_accounting mode. Only included
        # when explicitly set (never a silent 0.0 override) — otherwise cost
        # computation falls through to the litellm-table chain.
        self.rate_overrides: dict[str, float] = {}
        for rate_key in (
            "input_cost_per_token",
            "output_cost_per_token",
            "cached_input_cost_per_token",
            "cache_creation_cost_per_token",
        ):
            rate_value = config.get(rate_key)
            if rate_value is None:
                continue
            try:
                self.rate_overrides[rate_key] = float(rate_value)
            except (TypeError, ValueError):
                logger.warning(
                    f"Ignoring malformed rate override {rate_key}={rate_value!r} "
                    "(not a number); falling back to table pricing for this rate"
                )
        # Total USD the run may spend in cost_accounting mode. 0 means "log
        # only, no cap" (mirrors token_budget's 0-disables-budgeting meaning).
        try:
            self.cost_budget_usd: float = max(float(config.get("cost_budget_usd") or 0), 0)
        except (TypeError, ValueError):
            self.cost_budget_usd = 0.0
        self._cost_spent: float = 0.0
        # Calls whose usage was unreadable (so contributed $0 to _cost_spent).
        # Surfaced in usage so a cost_budget_usd cap that's silently breached
        # by unpriced calls is at least explainable after the fact.
        self._cost_unpriced_calls: int = 0

        self.extra_args: dict[str, Any] = run_input.orchestrator_extra_args or {}

        # Route through the Responses API (litellm.aresponses) when the
        # orchestrator model carries the "responses/" prefix. This lets
        # reasoning-model params (reasoning.effort/summary/etc.), supplied via
        # extra_args as {"extra_body": {"reasoning": {...}}}, reach the model
        # raw instead of being dropped by litellm's Chat Completions param
        # mapping. No prefix -> unchanged Chat Completions path (default).
        #
        # litellm_routes_responses instead sends the prefixed name whole over
        # Chat Completions and lets the configured OpenAI-compatible endpoint
        # (e.g. a LiteLLM proxy) resolve the model group to the Responses API
        # server-side. Self-routing sends the STRIPPED name, which resolves to
        # the Chat Completions group.
        self._litellm_routes_responses: bool = _coerce_bool(
            config.get("litellm_routes_responses"), default=False
        )
        self._use_responses: bool = (
            "responses/" in self.model and not self._litellm_routes_responses
        )
        # Prefix-stripped: litellm's rate and context tables cannot resolve a
        # prefixed form. Pricing, token counts and the window read this, never
        # the wire.
        self._api_model: str = self.model.replace("responses/", "")
        self._wire_model: str = (
            self.model if self._litellm_routes_responses else self._api_model
        )

        # ── Context-window management ────────────────────────────────────
        # Fraction of the model's input window above which the oldest turns
        # are dropped before the request is dispatched. 0.0 (the default)
        # disables the whole mechanism — including the
        # ContextWindowExceededError recovery in step() — so agent instances
        # configured before this field existed behave exactly as before.
        self.context_trim_utilization: float = _coerce_fraction(
            config.get("context_trim_utilization"),
            default=0.0,
            minimum=0.0,
            maximum=0.95,
        )
        # Once trimming fires, trim down to this fraction rather than merely
        # back under the limit — see DEFAULT_TRIM_FLOOR on why (prompt cache).
        self.context_trim_floor: float = _coerce_fraction(
            config.get("context_trim_floor"),
            default=DEFAULT_TRIM_FLOOR,
            minimum=0.2,
            maximum=0.9,
        )
        # Calibrated tokens of tool results a single step's tool-call batch may
        # deliver. 0 (the default) disables. Covers the one case the trim
        # cannot: tail-drop refuses to drop the newest turn, so a step whose
        # own results overflow the window leaves the trim over budget and the
        # request is rejected anyway.
        try:
            self.max_step_tool_result_tokens: int = max(
                int(config.get("max_step_tool_result_tokens") or 0), 0
            )
        except (TypeError, ValueError):
            self.max_step_tool_result_tokens = 0
        # Write an over-budget result to the workspace filesystem and hand back
        # a path, instead of refusing it. Off by default: the refusal is the
        # right answer for a narrowable call. Only worth enabling where atomic
        # fetches (id in, whole object out, no range parameter) are what
        # overflow — there the model has no narrower query to make, so a
        # refusal ends the task.
        self.spill_oversized_results: bool = _coerce_bool(
            config.get("spill_oversized_results"), default=False
        )
        # Comma-separated string or list; list("a,b") would yield
        # ['a', ',', 'b'], so the string must be split.
        disabled = config.get("disabled_tools") or []
        if isinstance(disabled, str):
            disabled = disabled.split(",")
        self.disabled_tools: list[str] = [
            str(name).strip() for name in disabled if name and str(name).strip()
        ]
        self._spill_counter: int = 0
        # The system prompt + original task prompt arrive as initial_messages
        # and must survive every trim.
        self._anchor_count: int = len(run_input.initial_messages)
        window, _source = resolve_max_input_tokens(self._api_model)
        self._max_input_tokens: int = window
        # Converts litellm's (Anthropic-blind, tool-schema-blind) estimate into
        # a projection of what the provider will actually bill, calibrated from
        # the provider's own prompt_tokens as the run proceeds.
        self._projector: PromptTokenProjector = PromptTokenProjector(self._api_model)
        # Raw estimate of the history handed to the current in-flight call,
        # held so the response's reported prompt_tokens can be paired with it.
        self._pending_prompt_estimate: int | None = None

        self.current_step: int = 0
        self.start_time: float | None = None
        self.status: AgentStatus = AgentStatus.PENDING
        self._usage_tracker: UsageTracker = UsageTracker(
            track_token_breakdown=True, model=self.model
        )

    async def _initialize_tools(self) -> None:
        """Load available tools from the tool provider."""
        tools = list(self.provider.list_tools())

        if self.disabled_tools:
            tools = [
                t for t in tools if t.get("function", {}).get("name") not in self.disabled_tools
            ]

        logger.info(f"Loaded {len(tools)} tools")
        self.tools = tools

    def _truncate_tool_message(self, msg: Any) -> Any:
        """Apply truncation to a tool message's content (dict-style only)."""
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            return msg

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = _truncate_output(content, self.max_output_lines, self.max_output_chars)
        elif isinstance(content, list):
            truncated_parts: list[dict[str, str]] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    truncated_text = _truncate_output(
                        str(part.get("text", "")),
                        self.max_output_lines,
                        self.max_output_chars,
                    )
                    truncated_parts.append({"type": "text", "text": truncated_text})
                else:
                    truncated_parts.append(part)
            msg["content"] = truncated_parts
        return msg

    # ── Context-window management ───────────────────────────────────

    def _context_management_enabled(self) -> bool:
        """Whether pre-dispatch trimming (and its overflow recovery) is on."""
        return self.context_trim_utilization > 0.0

    def _trim_limit(self) -> int:
        """Projected billed prompt tokens above which trimming fires."""
        return int(self._max_input_tokens * self.context_trim_utilization)

    def _trim_target(self) -> int:
        """Projected billed prompt tokens a trim brings the history down to."""
        return min(int(self._max_input_tokens * self.context_trim_floor), self._trim_limit())

    def _prepare_context(self) -> None:
        """Trim the history if needed, then record what is about to be sent.

        Runs before every dispatch, which is what makes the single-step
        cliffs survivable: a step that added 150k tokens is seen here, before
        the request that would have been rejected for it. No-op unless
        `context_trim_utilization` is set.
        """
        if not self._context_management_enabled():
            return
        estimate = count_tokens(self._api_model, self.messages)
        limit = self._trim_limit()
        projected = self._projector.project(estimate)
        if projected > limit:
            estimate, _ = self._trim_context(limit=limit, reason="pre_dispatch")
        self._pending_prompt_estimate = estimate

    def _trim_context(self, *, limit: int, reason: str, force: bool = False) -> tuple[int, int]:
        """Drop oldest turns until the projected prompt is under the floor.

        Budgets are expressed in projected (billed) tokens but measurement
        happens in raw estimates, so the limits are inverted through the
        projector rather than the counts being projected — the projection is
        affine, and projecting per-turn counts would charge its offset once
        per turn.

        Returns ``(raw estimate of the resulting history, turns dropped)``.
        Zero turns dropped means the trim freed nothing — the caller has to
        decide whether that is survivable, and on the recovery path it is not.
        """
        target = self._trim_target()
        result = trim_to_budget(
            self.messages,
            anchor_count=self._anchor_count,
            limit=self._projector.invert(limit),
            floor=self._projector.invert(target),
            count=lambda msgs: count_tokens(self._api_model, msgs),
            force=force,
        )
        if not result.trimmed:
            logger.warning(
                "Context over budget but nothing droppable: the anchored "
                "messages plus the newest turn already exceed the limit "
                f"(projected {self._projector.project(result.tokens_before)} "
                f"> {limit} tokens)"
            )
            return result.tokens_after, 0
        self.messages = result.messages
        self._usage_tracker.track_compaction()
        self.messages.append(
            {
                "role": "user",
                "content": CONTEXT_TRIM_NOTICE_TEMPLATE.format(
                    dropped_turns=result.dropped_turns,
                    dropped_messages=result.dropped_messages,
                ),
            }
        )
        logger.warning(
            f"Context trim ({reason}): dropped {result.dropped_turns} oldest "
            f"assistant turn(s) / {result.dropped_messages} message(s); "
            f"projected prompt {self._projector.project(result.tokens_before)} "
            f"-> {self._projector.project(result.tokens_after)} tokens "
            f"(limit {limit}, target {target})"
        )
        return count_tokens(self._api_model, self.messages), result.dropped_turns

    def _observe_prompt_tokens(self, calls_before: int) -> None:
        """Feed the provider's reported prompt_tokens back into the projector.

        This is the only source of truth about token density: litellm's
        estimator under-counts Anthropic by 32-47% and never sees the tool
        schemas at all.
        """
        estimate = self._pending_prompt_estimate
        self._pending_prompt_estimate = None
        if estimate is None:
            return
        call_log = self._usage_tracker.call_log
        if len(call_log) <= calls_before or not call_log:
            return
        reported = call_log[-1].get("prompt_tokens", 0)
        if reported <= 0:
            return
        self._projector.observe(estimate, reported)

    def _recover_from_context_overflow(self, exc: Exception) -> bool:
        """Trim and continue instead of ending the run on a 400.

        The retry decorator in `runner.llm` deliberately refuses to retry
        this error — resending identical bytes can never clear it — so
        recovery has to *change* the bytes, which only the harness can do.
        Returning from step() without an assistant message leaves the history
        as it was minus the dropped turns, and run()'s loop retries on the
        next iteration: the 400 costs a step, not the run.

        Returns True only when the trim actually dropped something. False
        means the anchored messages plus the newest turn are themselves over
        the limit, so the retry would carry the byte-identical prompt the
        provider just refused — the caller must re-raise rather than spend
        the remaining `max_steps` collecting the same 400 over and over.
        """
        estimate = self._pending_prompt_estimate
        self._pending_prompt_estimate = None
        logger.warning(f"Context window exceeded; trimming history and retrying: {exc!r}")
        if estimate is not None:
            # The provider just contradicted our projection for this exact
            # history. Recalibrate before deciding how much to drop, or the
            # trim inherits the same under-estimate that let it through.
            self._projector.observe_overflow(estimate, self._max_input_tokens)
        _, dropped_turns = self._trim_context(
            limit=self._trim_target(),
            reason="context_window_exceeded",
            force=True,
        )
        if not dropped_turns:
            logger.error(
                "Context window exceeded and the trim freed nothing: the "
                "anchored messages plus the newest turn are already over the "
                "limit. The next request would be byte-identical to the one "
                "just refused, so this fails now rather than repeating the "
                f"same 400 for every remaining step: {exc!r}"
            )
        return bool(dropped_turns)

    # ── Per-step tool-result budget ─────────────────────────────────

    def _step_tool_result_budget(self) -> int | None:
        """Calibrated tokens of tool results this step's batch may deliver.

        ``None`` means unlimited — the mechanism is off. A budget of ``0`` is
        a real budget with nothing left in it, and refuses the whole batch:
        the history is already at the trim limit and the newest turn is the
        one thing tail-drop will not drop, so delivering here produces a
        request that cannot be repaired by trimming at all.

        When context management is on, the configured cap is clamped to the
        headroom actually left, measured from the provider's own reported
        prompt size for the request we just sent — the exact number these
        results are about to be piled on top of.
        """
        if self.max_step_tool_result_tokens <= 0:
            return None
        budget = self.max_step_tool_result_tokens
        if self._context_management_enabled():
            call_log = self._usage_tracker.call_log
            reported = call_log[-1].get("prompt_tokens", 0) if call_log else 0
            if reported > 0:
                budget = min(budget, max(self._trim_limit() - reported, 0))
        return budget

    async def _handle_over_budget_result(
        self,
        msg: Any,
        *,
        tool_name: str,
        budget: int,
        used: int,
        tokens: int,
        spill_payload: str | None = None,
    ) -> tuple[Any, bool]:
        """Spill an over-budget tool result to the filesystem, else refuse it.

        Returns the rewritten message and whether it was spilled, so the caller
        can count a recovered result separately from a discarded one.

        The message keeps its role/tool_call_id/name either way, so the
        ``tool_use`` it answers still has a ``tool_result`` — dropping it
        outright would be rejected by the provider.

        ``msg`` is the truncated copy: it is what the preview is cut
        from and what the token cost was priced on. ``spill_payload`` is the
        ORIGINAL result and is what gets written. They differ whenever
        either truncation cap fired first, and writing the truncated copy while
        calling it complete would be the exact mistake
        ``STEP_TOOL_BUDGET_REFUSAL_TEMPLATE`` exists to avoid — worse for a
        base64 download, which does not decode once it is cut short.

        Spilling is opt-in and best-effort: with the flag off, or if the write
        fails for any reason, this is the refusal it has always been.
        """
        if not isinstance(msg, dict):
            return msg, False
        text = _message_text(msg)

        common = {
            "tokens": tokens,
            "chars": len(text),
            "remaining": max(budget - used, 0),
            "budget": budget,
            "preview_chars": _REFUSAL_PREVIEW_CHARS,
            "preview": text[:_REFUSAL_PREVIEW_CHARS],
        }

        # No fallback to `text`: that is the already-truncated copy, and
        # writing it under a "complete" notice is the bug this guards.
        payload = spill_payload
        if self.spill_oversized_results and payload:
            self._spill_counter += 1
            path = spill_to_workspace(
                workspace_dir=self._workspace_dir,
                tool_name=tool_name,
                index=self._spill_counter,
                payload=payload,
            )
            if path is not None:
                spilled = STEP_TOOL_BUDGET_SPILL_TEMPLATE.format(
                    path=path, **{**common, "chars": len(payload)}
                )
                msg["content"] = [{"type": "text", "text": spilled}]
                return msg, True

        refusal = STEP_TOOL_BUDGET_REFUSAL_TEMPLATE.format(**common)
        msg["content"] = [{"type": "text", "text": refusal}]
        return msg, False

    # ── LLM call ────────────────────────────────────────────────────

    async def _get_response(self) -> dict[str, Any] | None:
        """Get one assistant reply via the Responses API or Chat Completions.

        Returns None when the reply was empty/invalid so the caller can nudge
        the model to continue and retry.
        """
        if self._use_responses:
            return await self._get_responses_reply()

        result = self._llm_fn(
            self._wire_model,
            self.messages,
            self.tools,
            self.llm_response_timeout,
            self.extra_args,
            trajectory_id=self.trajectory_id,
        )
        if inspect.isawaitable(result):
            result = await result
        response = result
        self._usage_tracker.track(response)
        if self.cost_accounting_active:
            self._track_step_cost(response)
        logger.debug(f"Response: {response}")

        choices = getattr(response, "choices", None)
        if choices is None and isinstance(response, dict):
            choices = response.get("choices")
        if not choices:
            return None
        first = choices[0]
        message = getattr(first, "message", None)
        if message is None and isinstance(first, dict):
            message = first.get("message")
        if message is None:
            return None
        return _normalize_assistant_message(message)

    async def _get_responses_reply(self) -> dict[str, Any] | None:
        """Call the OpenAI Responses API and map the output back to a Chat
        Completions-style assistant message so the shared tool loop below is
        unchanged. Returns None on empty/unusable output (no tool calls and no
        text) so step() nudges the model to continue — parity with the Chat
        Completions branch.

        Reasoning params travel in ``self.extra_args`` as
        ``{"extra_body": {"reasoning": {...}}}``; ``call_responses_api``
        forwards ``extra_body`` raw to ``litellm.aresponses``, so reasoning
        fields (effort/summary/etc.) reach the model without being stripped by
        litellm's param mapping.
        """
        replays = self.replay_reasoning or asks_for_encrypted_reasoning(self.extra_args)
        responses_input = convert_messages_for_responses_api(
            self.messages, replay_reasoning=replays
        )
        responses_tools = [to_responses_tool(t) for t in self.tools]
        result = self._responses_fn(
            self._wire_model,
            responses_input,
            responses_tools,
            self.llm_response_timeout,
            self.extra_args,
            trajectory_id=self.trajectory_id,
            keep_encrypted_reasoning=self.replay_reasoning,
        )
        if inspect.isawaitable(result):
            result = await result
        response = result
        self._usage_tracker.track_from_dict(
            response.model_dump() if hasattr(response, "model_dump") else dict(response)
        )
        if self.cost_accounting_active:
            self._track_step_cost(response)
        parsed = parse_responses_api_output(response)
        # Prefer the summary-aware extraction (handles the summary_text path that
        # parse_responses_api_output misses).
        summary = extract_reasoning_summary(response)
        if summary:
            parsed.reasoning_content = summary
        message = responses_output_to_message(parsed)
        # The endpoint may supply encrypted reasoning even without `include`.
        if self.replay_reasoning and (
            reasoning_items := [
                {k: v for k, v in item.items() if v is not None}
                for item in parsed.raw_output_items
                if item.get("type") == "reasoning" and item.get("encrypted_content")
            ]
        ):
            message["reasoning_items"] = reasoning_items
        elif not replays:
            # The shared parser keeps encrypted items; this agent replays only on request.
            message.pop("reasoning_items", None)
        if message.get("content") is None:
            message["content"] = ""
        # Truly-empty reply (no tool calls, no text, no reasoning) -> let step()
        # nudge + retry. A reasoning-only turn is NOT empty: keep it so its
        # reasoning is preserved and step() handles it (parity with the Chat
        # Completions branch, which returns the parsed message).
        if (
            not message.get("tool_calls")
            and not (message.get("content") or "").strip()
            and not message.get("reasoning_content")
            and not message.get("reasoning_items")
        ):
            # `parsed.raw_output_items` is already model_dumped, so these are
            # dicts whatever the provider returned. Type names, never content.
            self._empty_reply_had_output = bool(parsed.raw_output_items)
            item_types = [i.get("type") for i in parsed.raw_output_items if isinstance(i, dict)]
            logger.warning(
                f"Responses API reply mapped to nothing for {self._api_model}: "
                f"{len(parsed.raw_output_items)} output item(s), types={item_types}"
            )
            return None
        return message

    # ── Tool execution ──────────────────────────────────────────────

    async def _call_tool(self, name: str, tool_call: dict[str, Any]) -> Any:
        """Execute one tool call through the provider, off the event loop.

        Malformed JSON arguments never reach the tool: they come back as the
        gateway-style "Error executing tool {name}: {exc}" observation.
        """
        raw_args = (tool_call.get("function") or {}).get("arguments")
        try:
            arguments = json.loads(raw_args) if raw_args else {}
            if not isinstance(arguments, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as exc:
            return f"Error executing tool {name}: {exc}"
        return await asyncio.wait_for(
            asyncio.to_thread(self.provider.call_tool, name, arguments),
            timeout=self.tool_call_timeout,
        )

    def _observation_messages(
        self,
        observation: Any,
        tool_call_id: str | None,
        name: str,
        deferred_image_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Map a provider observation onto conversation messages.

        Atoll's replacement for upstream's ``content_blocks_to_messages``:
        str → tool-message text verbatim; dict/pydantic-dump → json.dumps;
        an image content object → a tool message pointing at a deferred user
        message that carries the image (tool messages cannot carry images).
        """
        if hasattr(observation, "mime_type") and hasattr(observation, "base64_data"):
            data_uri = f"data:{observation.mime_type};base64,{observation.base64_data}"
            deferred_image_messages.append(
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": data_uri}}],
                }
            )
            text = "Image result attached in the following message."
        elif isinstance(observation, str):
            text = observation
        else:
            if hasattr(observation, "model_dump"):
                observation = observation.model_dump()
            try:
                text = json.dumps(observation, ensure_ascii=False, default=str)
            except TypeError:
                text = f"Error executing tool {name}: non-serializable result"
        return [{"role": "tool", "tool_call_id": tool_call_id, "name": name, "content": text}]

    # ── Step ────────────────────────────────────────────────────────

    async def step(self):
        """Execute a single step of the agent loop."""
        self.current_step += 1

        # Trim before the LLM call so the request stays under the model's
        # input-token limit. Long tool-heavy trajectories accumulate results +
        # thinking until the window is exhausted, and a single step's parallel
        # tool batch can add 150k tokens on its own.
        self._prepare_context()

        calls_before = len(self._usage_tracker.call_log)
        try:
            response_message = await self._get_response()
        except ContextWindowExceededError as e:
            if not self._context_management_enabled():
                logger.error(f"Error generating response: {e!r}")
                raise
            # Recovery that freed nothing is not recovery: returning here would
            # resend the identical prompt next iteration and collect the
            # identical 400 until max_steps, turning a fast failure into a slow
            # one. Re-raise instead — run() records it exactly as it would with
            # trimming disabled.
            if not self._recover_from_context_overflow(e):
                raise
            return
        except Timeout:
            logger.error("Response timed out, continuing with next step")
            return
        except Exception as e:
            # Some providers (notably Gemini) return context overflow as a
            # plain BadRequestError instead of ContextWindowExceededError.
            if self._context_management_enabled() and _is_context_window_error(e):
                if self._recover_from_context_overflow(e):
                    return
                raise
            logger.error(f"Error generating response: {e!r}")
            raise e
        self._observe_prompt_tokens(calls_before)

        if response_message is None:
            self._consecutive_reasoning_only_turns = 0
            self._consecutive_empty_replies += 1
            seen = self._consecutive_empty_replies
            if seen >= MAX_CONSECUTIVE_EMPTY_REPLIES:
                self._abort_reason = (
                    f"no usable reply from {self.model} in {seen} consecutive steps"
                )
                logger.warning(
                    f"LLM returned invalid/empty output "
                    f"({seen}/{MAX_CONSECUTIVE_EMPTY_REPLIES}), giving up"
                )
                return
            logger.warning(
                f"LLM returned invalid/empty output "
                f"({seen}/{MAX_CONSECUTIVE_EMPTY_REPLIES}), prompting to continue"
            )
            self.messages.append({"role": "user", "content": "continue"})
            return
        self._consecutive_empty_replies = 0
        self._empty_reply_had_output = False

        tool_calls = response_message.get("tool_calls")

        if response_message.get("reasoning_content"):
            logger.info(f"Reasoning: {response_message['reasoning_content']}")

        if response_message.get("content") and tool_calls:
            logger.info(response_message["content"])

        self.messages.append(response_message)

        if tool_calls:
            self._consecutive_reasoning_only_turns = 0
            pre_tool_len = len(self.messages)
            deferred_image_messages: list[dict[str, Any]] = []
            # Priced once per batch, before any result comes back: a 48-call
            # parallel batch can add 150k tokens between one pre-dispatch trim
            # and the next, which is more than the trim can repair on its own.
            step_result_budget = self._step_tool_result_budget()
            step_result_tokens = 0
            step_refusals = 0
            step_spills = 0
            for tool_call in tool_calls:
                name = (tool_call.get("function") or {}).get("name") or "unknown"

                logger.info(f"Calling tool {name}")

                if name in self.disabled_tools:
                    disabled_msg = f"Error: tool '{name}' is disabled for this run."
                    logger.warning(disabled_msg)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": name,
                            "content": disabled_msg,
                        }
                    )
                    continue

                try:
                    observation = await self._call_tool(name, tool_call)
                except TimeoutError:
                    logger.error(f"Tool call {name} timed out")
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": name,
                            "content": "Tool call timed out",
                        }
                    )
                    continue
                except Exception as e:
                    logger.error(f"Error calling tool {name}: {e!r}")
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": name,
                            "content": f"Error calling tool: {e!r}",
                        }
                    )
                    continue

                messages = self._observation_messages(
                    observation, tool_call.get("id"), name, deferred_image_messages
                )

                # Snapshot the text BEFORE truncating. _truncate_tool_message
                # rewrites msg["content"] in place and hands back the same
                # object, so once it has run there is no untruncated copy
                # left to read — and a spill must write the whole result,
                # not the truncated one.
                pre_truncation_texts = [_spillable_text(msg) for msg in messages]

                # Apply truncation to tool result messages
                truncated_messages = [self._truncate_tool_message(msg) for msg in messages]

                if step_result_budget is not None:
                    priced: list[Any] = []
                    # Pricing and the preview use the truncated copy, which
                    # is what the model would have received inline; the
                    # spill uses the snapshot taken above.
                    for original_text, result_msg in zip(
                        pre_truncation_texts, truncated_messages, strict=True
                    ):
                        cost = self._projector.scale(count_tokens(self._api_model, [result_msg]))
                        if step_result_tokens + cost > step_result_budget:
                            (
                                handled,
                                spilled,
                            ) = await self._handle_over_budget_result(
                                result_msg,
                                tool_name=name,
                                budget=step_result_budget,
                                used=step_result_tokens,
                                tokens=cost,
                                spill_payload=original_text,
                            )
                            # Counted by outcome, not by "went over budget":
                            # a spilled result reached the model by another
                            # route, and rolling it into step_refusals would
                            # make the rollout unable to tell a recovered
                            # result from a discarded one.
                            if spilled:
                                step_spills += 1
                            else:
                                step_refusals += 1
                            logger.warning(
                                f"Tool {name} result "
                                f"{'spilled to filesystem' if spilled else 'refused'}: "
                                f"~{cost} token(s) would exceed this step's "
                                f"tool-result budget "
                                f"({step_result_tokens}/{step_result_budget} used)"
                            )
                            priced.append(handled)
                        else:
                            step_result_tokens += cost
                            priced.append(result_msg)
                    truncated_messages = priced

                logger.info(f"Tool {name} called successfully")

                self.messages.extend(truncated_messages)
            if step_refusals or step_spills:
                logger.warning(
                    f"{step_refusals} tool result(s) refused and {step_spills} "
                    f"spilled to the filesystem this step to stay within a "
                    f"{step_result_budget}-token tool-result budget"
                )
            self.messages.extend(deferred_image_messages)
            self._track_tool_outputs(self.messages[pre_tool_len:])
        else:
            self._handle_no_tool_calls(response_message)

    def _track_tool_outputs(self, new_messages: list[Any]) -> None:
        """Count this step's tool-result text + images for the breakdown.

        Tool-result text comes from role=="tool" messages; tool-result images
        are counted live from their data-URIs, wherever they land (embedded in
        the tool message or as deferred user messages). The text here is
        already truncated by ``_truncate_tool_message`` — i.e. what the model
        was actually billed for. No-op unless breakdown tracking is on.
        """
        tool_texts: list[str] = []
        image_uris: list[str] = []
        for m in new_messages:
            content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
            role = m.get("role") if isinstance(m, dict) else getattr(m, "role", None)
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        url = (b.get("image_url") or {}).get("url")
                        if url:
                            image_uris.append(url)
            if role != "tool":
                continue
            if isinstance(content, str):
                tool_texts.append(content)
            elif isinstance(content, list):
                tool_texts.append(
                    " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                )
        if tool_texts:
            self._usage_tracker.track_tool_output(" ".join(tool_texts))
        for uri in image_uris:
            self._usage_tracker.track_tool_output_image(uri)

    def _track_step_cost(self, response: Any) -> None:
        """Compute and log this step's real $ cost (cost_accounting mode only).

        Always logs — cost_budget_usd=0 means "no cap", not "don't log".
        Called with either a Chat Completions ``ModelResponse`` (from
        ``_get_response``) or a raw Responses-API response object (from
        ``_get_responses_reply``) — ``compute_call_cost_usd`` reads either
        shape, and prices with ``self._api_model`` (the prefix-stripped name
        litellm's rate table can resolve).

        A call with unreadable usage contributes $0 to _cost_spent (there's
        no token count to price), but is counted in _cost_unpriced_calls so a
        cost_budget_usd cap silently breached by unpriced calls is still
        explainable after the fact — real spend must never look like it was
        simply never incurred.
        """
        call_cost = compute_call_cost_usd(self._api_model, response, self.rate_overrides)
        if call_cost is None:
            self._cost_unpriced_calls += 1
            logger.warning("Cost unavailable for this call (unreadable usage); not counted")
            return
        self._cost_spent += call_cost
        logger.info(f"Step cost: ${call_cost:.6f} (cumulative: ${self._cost_spent:.6f})")

    def _tokens_spent(self) -> int:
        """Exact provider-reported prompt+completion tokens spent so far."""
        return self._usage_tracker.prompt_tokens + self._usage_tracker.completion_tokens

    def _cost_budget_warning(self) -> tuple[bool, str, float]:
        """Compose the cost-budget warning (or exhausted variant) for
        cost_accounting mode. Mirrors the token-budget branch below, just
        priced in $ instead of tokens. Returns (exhausted, warning_msg,
        cost_remaining)."""
        cost_remaining = max(self.cost_budget_usd - self._cost_spent, 0.0)
        exhausted = bool(self.cost_budget_usd) and self._cost_spent >= self.cost_budget_usd
        if exhausted:
            warning_msg = COST_BUDGET_EXHAUSTED_TEMPLATE.format(
                cost_budget=self.cost_budget_usd,
                cost_spent=self._cost_spent,
            )
        else:
            warning_msg = COST_BUDGET_WARNING_TEMPLATE.format(
                cost_remaining=cost_remaining,
                cost_budget=self.cost_budget_usd,
            )
        return exhausted, warning_msg, cost_remaining

    def _inject_step_warning(self, step: int) -> bool:
        """Inject the per-step turn/token/cost budget warning as a user message.

        `turn_warnings_enabled` is orthogonal to `accounting_mode` — it may be
        combined with any mode, including cost_accounting. Only called when
        turn warnings, a token budget, or a cost budget (in cost_accounting
        mode) are enabled. Returns True once the active budget is exhausted,
        granting one final step to answer; turn_warnings_enabled alone never
        triggers exhaustion.
        """
        remaining = self.max_steps - step
        if self.cost_accounting_active:
            if not self.cost_budget_usd:
                # cost_accounting active but uncapped (log-only): the cost
                # clause has nothing meaningful to say ("$0.0000 of $0.0000
                # remaining" is nonsense), so only the turn-count reminder
                # applies here. Reaching this branch at all implies
                # turn_warnings_enabled is True — that's the only way the
                # run() loop's gate could have fired for this combination.
                warning_msg = TURN_WARNING_TEMPLATE.format(remaining=remaining)
                self.messages.append({"role": "user", "content": warning_msg})
                logger.info(warning_msg)
                return False
            exhausted, cost_msg, cost_remaining = self._cost_budget_warning()
            if exhausted:
                warning_msg = cost_msg
            else:
                parts: list[str] = []
                if self.turn_warnings_enabled:
                    parts.append(TURN_WARNING_TEMPLATE.format(remaining=remaining))
                parts.append(cost_msg)
                warning_msg = " ".join(parts)
            self.messages.append({"role": "user", "content": warning_msg})
            logger.info(warning_msg)
            return exhausted

        tokens_spent = self._tokens_spent()
        tokens_remaining = (
            max(self.token_budget - tokens_spent, 0) if self.token_budget else None
        )
        exhausted = bool(self.token_budget) and tokens_spent >= self.token_budget
        if exhausted:
            # Budget spent: this is the final step.
            warning_msg = TOKEN_BUDGET_EXHAUSTED_TEMPLATE.format(
                token_budget=self.token_budget,
                tokens_spent=tokens_spent,
            )
        else:
            # Compose the enabled pieces: steps remaining and/or budget left.
            parts = []
            if self.turn_warnings_enabled:
                parts.append(TURN_WARNING_TEMPLATE.format(remaining=remaining))
            if tokens_remaining is not None:
                parts.append(
                    TOKEN_BUDGET_WARNING_TEMPLATE.format(
                        tokens_remaining=tokens_remaining,
                        token_budget=self.token_budget,
                    )
                )
            warning_msg = " ".join(parts)
        # Inject as a user message and mirror it to the log.
        self.messages.append({"role": "user", "content": warning_msg})
        logger.info(warning_msg)
        return exhausted

    def _record_terminal_error(self, text: str, fault: dict[str, Any] | None = None) -> None:
        """Record the terminal error (and its structured fault) on the run.

        Upstream captures these through a loguru ERROR sink; Atoll sets them
        directly. Set together, like the sink does: a later error without a
        fault clears any earlier descriptor rather than pairing stale
        structure with new prose.
        """
        logger.error(text)
        self._terminal_error = text
        self._terminal_fault = dict(fault) if fault else None

    def _build_output(self) -> AgentTrajectoryOutput:
        usage = self._usage_tracker.to_dict()
        usage["accounting_mode"] = self.accounting_mode
        # token_budget/tokens_spent are recorded only when budgeting was on.
        if self.token_budget:
            usage["token_budget"] = self.token_budget
            usage["tokens_spent"] = self._tokens_spent()
        # cost_* fields are recorded only in cost_accounting mode; always
        # included together in that mode, regardless of whether a hard cap
        # (cost_budget_usd) was also set — cost is never silently unlogged.
        if self.cost_accounting_active:
            usage["cost_rate_overrides"] = self.rate_overrides
            usage["cost_usd_spent"] = self._cost_spent
            usage["cost_budget_usd"] = self.cost_budget_usd
            usage["cost_unpriced_calls"] = self._cost_unpriced_calls
        return AgentTrajectoryOutput(
            messages=list(self.messages),
            status=AgentStatus(self.status),
            time_elapsed=time.time() - self.start_time if self.start_time else 0,
            usage=usage,
            final_answer=self._final_answer,
            error=self._terminal_error,
            fault=self._terminal_fault,
        )

    async def run(self) -> AgentTrajectoryOutput:
        """Run the agent loop until completion or timeout."""
        try:
            async with asyncio.timeout(self.timeout):
                logger.info(f"Starting agent loop with model {self.model}")

                await self._initialize_tools()

                self.start_time = time.time()
                self.status = AgentStatus.RUNNING

                budget_final_turn_taken = False
                for i in range(self.max_steps):
                    if self._finalized:
                        logger.info(f"Agent loop was finalized after {i + 1} steps")
                        break
                    if budget_final_turn_taken:
                        break
                    if self._abort_reason is not None:
                        break
                    # Per-step warnings are opt-in: with neither turn
                    # warnings nor a token_budget/cost_budget_usd configured
                    # the loop runs exactly as before (no injected user
                    # messages).
                    if (
                        self.turn_warnings_enabled
                        or self.token_budget
                        or (self.cost_accounting_active and self.cost_budget_usd)
                    ):
                        budget_final_turn_taken = self._inject_step_warning(i)
                    logger.info(f"Starting step {i + 1}")
                    await self.step()

                if not self._finalized:
                    if self._abort_reason is not None:
                        # EMPTY_RESPONSE means the model said nothing. Raw
                        # output the mapping could not read is a harness
                        # fault with no kind, so it falls back to the text.
                        self._record_terminal_error(
                            f"Agent loop abandoned: {self._abort_reason}",
                            fault=None if self._empty_reply_had_output else empty_response(),
                        )
                    elif budget_final_turn_taken and self.cost_accounting_active:
                        self._record_terminal_error(
                            f"Agent loop not finalized after exhausting cost "
                            f"budget of ${self.cost_budget_usd:.4f}"
                        )
                    elif budget_final_turn_taken:
                        self._record_terminal_error(
                            f"Agent loop not finalized after exhausting token "
                            f"budget of {self.token_budget}"
                        )
                    else:
                        self._record_terminal_error(
                            f"Agent loop was not finalized after {self.max_steps} steps"
                        )
                    self.status = AgentStatus.FAILED
                else:
                    self.status = AgentStatus.COMPLETED

                return self._build_output()

        except TimeoutError:
            self._record_terminal_error(
                f"Agent run timed out after {self.timeout} seconds",
                fault=budget_exhausted(Budget.WALL_CLOCK, self.timeout),
            )
            self.status = AgentStatus.ERROR
            return self._build_output()

        except asyncio.CancelledError:
            self._record_terminal_error("Agent run cancelled")
            self.status = AgentStatus.CANCELLED
            return self._build_output()

        except Exception as e:
            self._record_terminal_error(f"Error running agent: {e!r}")
            if is_system_error(e):
                self.status = AgentStatus.ERROR
            else:
                self.status = AgentStatus.FAILED
            return self._build_output()


async def run(
    run_input: AgentRunInput,
    provider: ToolProvider,
    llm_fn: Callable[..., Any] | None = None,
    responses_fn: Callable[..., Any] | None = None,
) -> AgentTrajectoryOutput:
    """
    Entry point for the loop truncated tools agent.

    Args:
        run_input: The input configuration for the agent run
        provider: The tool provider the loop executes tools through
        llm_fn: Optional Chat Completions callable override (sync or async)
        responses_fn: Optional Responses API callable override (sync or async)

    Returns:
        AgentTrajectoryOutput with status, messages, and metrics
    """
    agent = LoopTruncatedToolsAgent(run_input, provider, llm_fn, responses_fn)
    return await agent.run()
