# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Models for agent execution, ported from Archipelago's runner/agents/models.py.

Divergences from upstream:

* Messages are plain dicts throughout Atoll (upstream mixes litellm TypedDicts
  with pydantic ``Message`` objects), so the Litellm* type aliases, the
  ``ValidatorIterator`` materialization and the tool-message image
  normalization validators are dropped — there is nothing lazy or
  schema-restricted to normalize.
* ``AgentRunInput`` carries ``workspace_dir`` instead of upstream's
  ``mcp_gateway_url``/``mcp_gateway_auth_token``/``mcp_gateway_actor_id``:
  Atoll's per-run workspace IS the sandbox filesystem (shared with the
  code-execution container at /filesystem and presented to filesystem tools
  as '/'), so it is both the tool mount point and the spill target.
* ``parent_trajectory_output``, ``custom_args``, ``task_custom_fields``,
  ``inner_agent_config``, ``rest_services``, ``AgentConfigIds``/``AgentDefn``
  and the transcript-timing metadata are dropped (multi-turn/Studio-only).
* ``final_answer``/``error``/``fault`` on ``AgentTrajectoryOutput`` are filled
  directly by the agent instead of captured by a loguru logging sink; the
  fault descriptors (``budget_exhausted``, ``empty_response``) are ported here
  from upstream's runner/utils/logging/terminal_error.py, trimmed to the kinds
  this agent emits.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class AgentStatus(StrEnum):
    """Status of an agent run."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    ERROR = "error"


def get_msg_role(msg: Any) -> str:
    """Get role from either a dict message or an object with a role attribute."""
    if isinstance(msg, dict):
        return msg.get("role", "")
    return getattr(msg, "role", "")


def get_msg_content(msg: Any) -> Any:
    """Get content from either a dict message or an object with a content attribute."""
    if isinstance(msg, dict):
        return msg.get("content")
    return getattr(msg, "content", None)


def content_to_str(content: Any) -> str:
    """Normalize message content to a string.

    Some providers (e.g. Anthropic) return content as a list of blocks
    like [{'type': 'text', 'text': '...'}]. Use this when you need a plain string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts) if parts else ""
    return str(content)


class FaultKind(StrEnum):
    """What went wrong, as the agent knows it."""

    BUDGET_EXHAUSTED = "budget_exhausted"
    EMPTY_RESPONSE = "empty_response"


class Budget(StrEnum):
    """Which limit ran out. One fault class; this says what to raise."""

    TOKENS = "tokens"
    STEPS = "steps"
    WALL_CLOCK = "wall_clock"
    COST = "cost"


def budget_exhausted(budget: Budget, limit: float | int | None = None) -> dict[str, Any]:
    """Descriptor for a run that hit a limit we imposed.

    Built here, not spelled per call site: an unrecognised `kind` falls back to
    the text by design, so a typo would be silent.
    """
    fault: dict[str, Any] = {
        "kind": FaultKind.BUDGET_EXHAUSTED.value,
        "budget": budget.value,
    }
    if limit is not None:
        fault["limit"] = limit
    return fault


def empty_response() -> dict[str, Any]:
    """Descriptor for a model that returned no content and no tool calls."""
    return {"kind": FaultKind.EMPTY_RESPONSE.value}


class AgentRunInput(BaseModel):
    """Input to an agent implementation."""

    trajectory_id: str

    # OpenAI-shaped message dicts the conversation opens with (system prompt +
    # task prompt). They anchor every context trim.
    initial_messages: list[Any]

    # Atoll's replacement for upstream's mcp_gateway_url: the per-run workspace
    # directory. Tools resolve '/'-rooted paths against it, the code-execution
    # container mounts it at /filesystem, and oversized tool results spill into it.
    workspace_dir: str

    orchestrator_model: str
    orchestrator_extra_args: dict[str, Any] | None = None
    agent_config_values: dict[str, Any] = Field(default_factory=dict)


class AgentTrajectoryOutput(BaseModel):
    """Output from an agent run."""

    messages: list[Any]
    output: dict[str, Any] | None = None
    status: AgentStatus
    time_elapsed: float
    usage: dict[str, Any] | None = None
    # The last final answer the agent emitted (upstream denormalizes this via a
    # logging sink; Atoll's agent sets it directly). None when none was emitted.
    final_answer: str | None = None
    # Terminal error prose; None on a clean run.
    error: str | None = None
    # The same failure stated structurally, e.g. {"kind": "budget_exhausted",
    # "budget": "wall_clock"}.
    fault: dict[str, Any] | None = None
