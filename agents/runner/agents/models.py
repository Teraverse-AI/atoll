# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Path shim for upstream ``runner/agents/models.py``.

Upstream's module also carries agent-definition models that drag the Studio
report/normalization machinery; the vendored LLM layer needs only the message
type aliases, the ``get_msg_*`` accessors and the ``AgentConfigIds`` registry,
which are copied verbatim from upstream here.
"""

from enum import StrEnum
from typing import Any

from litellm.types.llms.openai import AllMessageValues
from litellm.types.utils import Message
from openai.types.responses.easy_input_message_param import EasyInputMessageParam

# LiteLLM message types for agent execution:
# - InputMessage (AllMessageValues): TypedDict for Chat Completions API requests
# - ResponsesInputMessage (EasyInputMessageParam): TypedDict for Responses API requests
# - OutputMessage (Message): Pydantic model from API responses, used for new messages
# - AnyMessage: Union of all three, used for trajectory output (includes input + generated)
LitellmInputMessage = AllMessageValues
LitellmResponsesInputMessage = EasyInputMessageParam
LitellmOutputMessage = Message
LitellmAnyMessage = (
    LitellmInputMessage | LitellmResponsesInputMessage | LitellmOutputMessage
)


def get_msg_role(msg: LitellmAnyMessage) -> str:
    """Get role from either TypedDict or Pydantic Message."""
    if isinstance(msg, Message):
        return msg.role
    return msg["role"]


def get_msg_content(msg: LitellmAnyMessage) -> Any:
    """Get content from either TypedDict or Pydantic Message."""
    if isinstance(msg, Message):
        return msg.content
    return msg.get("content")


def get_msg_attr(msg: LitellmAnyMessage, key: str, default: Any = None) -> Any:
    """Get arbitrary attribute from either TypedDict or Pydantic Message."""
    if isinstance(msg, Message):
        return getattr(msg, key, default)
    return msg.get(key, default)


class AgentConfigIds(StrEnum):
    """Registry of available agent implementation IDs (e.g., 'loop_agent')."""

    LOOP_AGENT = "loop_agent"
    LOOP_TRUNCATED_TOOLS_AGENT = "loop_truncated_tools_agent"
    REACT_TOOLBELT_AGENT = "react_toolbelt_agent"
    SINGLESHOT_AGENT = "singleshot_agent"
    STIRRUP_AGENT = "stirrup_agent"
    SINGLE_SHOT_MULTIMODAL = "single_shot_multimodal"
