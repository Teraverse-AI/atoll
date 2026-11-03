# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Chat Completions <-> OpenAI Responses API conversion and output parsing.

Ported from Archipelago's runner/agents/responses_agent/conversion.py plus the
two output helpers the loop needs from runner/agents/responses_agent/main.py
(``parse_responses_api_output`` / ``responses_output_to_message``). The rest of
the single-shot responses agent is not ported.

Divergences from upstream:

* ``responses_output_to_message`` returns a plain OpenAI-shaped dict instead of
  a litellm ``Message`` pydantic object — Atoll's history is plain dicts
  throughout (see runner/models.py).
* ``reasoning_replay_items``/``encrypted_reasoning_items`` are imported from
  ``runner.llm`` (upstream: runner/utils/llm.py); semantics unchanged.
"""

import logging
from typing import Any

from pydantic import BaseModel

from runner.llm import encrypted_reasoning_items, reasoning_replay_items

logger = logging.getLogger(__name__)


def _get(msg: Any, key: str) -> Any:
    """Read a field from either a dict message or a pydantic/object message."""
    if isinstance(msg, dict):
        return msg.get(key)
    return getattr(msg, key, None)


def _tool_output_text(content: Any, content_str: str) -> str:
    """Flatten a tool message's content into a string for function_call_output.

    Tool results may be stored as list-of-blocks (e.g. truncated text parts).
    ``str(list)`` would emit a Python repr, so join the text blocks explicitly;
    scalar content falls back to its string form.
    """
    if isinstance(content, list):
        return " ".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return content_str


def _to_responses_content_blocks(content: list[Any]) -> list[Any]:
    """Translate Chat Completions content blocks to Responses API input blocks.

    The Responses API rejects Chat-Completions block types (``image_url``,
    ``text``, ``file``) on ``input[...].content`` — it wants ``input_image`` /
    ``input_text`` / ``input_file``. The loop's deferred tool-image user
    messages arrive as raw ``image_url`` blocks, so translate here;
    already-Responses / unknown blocks pass through unchanged (idempotent).
    """
    out: list[Any] = []
    for block in content:
        if isinstance(block, str):
            out.append({"type": "input_text", "text": block})
            continue
        if not isinstance(block, dict):
            out.append(block)
            continue
        btype = block.get("type")
        if btype == "text":
            out.append({"type": "input_text", "text": block.get("text", "")})
        elif btype == "image_url":
            image_url = block.get("image_url")
            if isinstance(image_url, str):
                out.append({"type": "input_image", "image_url": image_url, "detail": "auto"})
            elif isinstance(image_url, dict) and image_url.get("url"):
                out.append(
                    {
                        "type": "input_image",
                        "image_url": image_url["url"],
                        "detail": image_url.get("detail", "auto"),
                    }
                )
            else:
                out.append(block)
        elif btype == "file":
            file_info = block.get("file") or {}
            file_block: dict[str, Any] = {"type": "input_file"}
            for k in ("file_data", "file_id", "filename"):
                if file_info.get(k):
                    file_block[k] = file_info[k]
            out.append(file_block)
        else:
            # Already-Responses blocks (input_text/input_image/input_file) and
            # anything unrecognized pass through unchanged.
            out.append(block)
    return out


def convert_messages_for_responses_api(
    messages: list[Any],
    replay_reasoning: bool | None = None,
) -> list[dict[str, Any]]:
    """Convert Chat Completions format messages to Responses API input format.

    - system -> {"role": "developer", "content": "..."}
    - user -> {"role": "user", "content": "..."}  (list content translated)
    - assistant (text only) -> {"role": "assistant", "content": "..."}
    - assistant with tool_calls -> function_call items
    - tool -> {"type": "function_call_output", "call_id": "...", "output": "..."}

    ``replay_reasoning=False`` never replays reasoning items; ``None`` replays
    whatever the message carries.
    """
    converted: list[dict[str, Any]] = []

    for msg in messages:
        role = _get(msg, "role") or ""
        content = _get(msg, "content")
        tool_calls = _get(msg, "tool_calls")

        # Ensure content is always a string, never None
        content_str = str(content) if content else ""

        if role == "system":
            converted.append({"role": "developer", "content": content_str})

        elif role == "user":
            # Multimodal/file content is a list of blocks. Translate any
            # Chat-Completions block types (image_url/text/file) to Responses
            # input blocks; already-Responses blocks pass through unchanged. This
            # is what keeps the loop's deferred tool-image messages (raw
            # image_url) from hitting the Responses API as an invalid type.
            if isinstance(content, list):
                converted.append(
                    {"role": "user", "content": _to_responses_content_blocks(content)}
                )
            else:
                converted.append({"role": "user", "content": content_str})

        elif role == "assistant":
            if replay_reasoning is not False:
                converted.extend(reasoning_replay_items(msg))
            if tool_calls:
                # Emit text content as assistant message if present
                if content_str:
                    converted.append({"role": "assistant", "content": content_str})

                # Convert each tool_call to a function_call item
                for tc in tool_calls:
                    if hasattr(tc, "id"):
                        # Pydantic ChatCompletionMessageToolCall
                        tc_id = getattr(tc, "id", "")
                        func = getattr(tc, "function", None)
                        tc_name = getattr(func, "name", "") if func else ""
                        tc_args = getattr(func, "arguments", "{}") if func else "{}"
                    else:
                        # dict format
                        tc_id = tc.get("id", "")
                        func = tc.get("function", {})
                        tc_name = func.get("name", "")
                        tc_args = func.get("arguments", "{}")

                    converted.append(
                        {
                            "type": "function_call",
                            "id": tc_id,
                            "call_id": tc_id,
                            "name": tc_name,
                            "arguments": tc_args or "{}",
                        }
                    )
            else:
                converted.append({"role": "assistant", "content": content_str})

        elif role == "tool":
            # Convert tool message to function_call_output. Flatten list-block
            # content to text so tool outputs aren't repr-stringified.
            tool_call_id = _get(msg, "tool_call_id")
            converted.append(
                {
                    "type": "function_call_output",
                    "call_id": tool_call_id or "",
                    "output": _tool_output_text(content, content_str),
                }
            )

        else:
            # Pass through unknown roles as-is
            converted.append({"role": role, "content": content_str})

    return converted


def to_responses_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert a Chat Completions tool definition to Responses API tool format.

    Chat Completions: {"type": "function", "function": {"name", "description", "parameters"}}
    Responses API:    {"type": "function", "name", "description", "parameters", "strict"}

    ``strict`` is always sent. Omitted, the Responses API makes every strict-compatible
    schema strict, and a large strict toolset comes back as an empty ``incomplete`` reply
    with zero usage. False matches Chat Completions and LiteLLM's chat-to-Responses bridge.
    """
    func = tool.get("function", {})
    result: dict[str, Any] = {
        "type": "function",
        "name": func.get("name"),
        "strict": func.get("strict") is True,
    }
    desc = func.get("description")
    if desc:
        result["description"] = desc
    params = func.get("parameters")
    if params:
        result["parameters"] = params
    return result


def extract_reasoning_summary(response: Any) -> str | None:
    """Extract reasoning summary from Responses API output items.

    The Responses API returns reasoning items with a `summary` field containing
    summary_text items when reasoning.summary is configured (e.g. "concise"/"auto").
    The shared parse_responses_api_output only checks the `content` field with
    reasoning_text items, so this handles the `summary` path.
    """
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else dict(response)
    )
    parts: list[str] = []
    for item in response_dict.get("output", []) or []:
        if item.get("type") != "reasoning":
            continue
        # Collect this item's text; the content fallback is scoped per-item so a
        # summary on an earlier item doesn't suppress a later item's content text.
        item_parts: list[str] = []
        # Primary: summary field (summary_text items)
        summary = item.get("summary") or []
        if isinstance(summary, list):
            for s in summary:
                if isinstance(s, dict) and s.get("type") == "summary_text":
                    text = s.get("text", "")
                    if text:
                        item_parts.append(text)
                elif hasattr(s, "type") and getattr(s, "type", None) == "summary_text":
                    text = getattr(s, "text", "")
                    if text:
                        item_parts.append(text)
        elif isinstance(summary, str) and summary:
            item_parts.append(summary)
        # Fallback: this item's content field (reasoning_text items)
        if not item_parts:
            for c in item.get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "reasoning_text":
                    text = c.get("text", "")
                    if text:
                        item_parts.append(text)
        parts.extend(item_parts)
    return "\n".join(parts) if parts else None


class ParsedResponsesOutput(BaseModel):
    """Parsed output from the Responses API."""

    # Text content from the response
    text: str = ""

    # Tool calls made by the model
    tool_calls: list[dict[str, Any]] = []

    # Web search calls (if web_search tool was used)
    web_search_calls: list[dict[str, Any]] = []

    # Function calls (legacy format)
    function_calls: list[dict[str, Any]] = []

    # Annotations (citations, file references, etc.)
    annotations: list[dict[str, Any]] = []

    # Reasoning/thinking content (if model supports it)
    reasoning_content: str | None = None

    # Raw output items for inspection
    raw_output_items: list[dict[str, Any]] = []


def parse_responses_api_output(response: Any) -> ParsedResponsesOutput:
    """
    Parse the Responses API response into a structured format.

    The Responses API returns an 'output' array with items of different types:
    - message: Contains text content and annotations
    - function_call: A function/tool call made by the model
    - web_search_call: A web search action
    - reasoning: Model's reasoning/thinking (some models)

    Args:
        response: The raw response from litellm.aresponses()

    Returns:
        ParsedResponsesOutput with all extracted content
    """
    response_dict = (
        response.model_dump() if hasattr(response, "model_dump") else dict(response)
    )

    result = ParsedResponsesOutput()
    text_parts: list[str] = []

    output_items = response_dict.get("output", []) or []
    result.raw_output_items = output_items

    for output_item in output_items:
        item_type = output_item.get("type", "")

        if item_type == "message":
            # Extract text content from message
            for content_item in output_item.get("content", []):
                content_type = content_item.get("type", "")

                if content_type == "output_text":
                    text = content_item.get("text", "")
                    if text:
                        text_parts.append(text)

                    # Extract annotations from text content
                    annotations = content_item.get("annotations", [])
                    result.annotations.extend(annotations)

                elif content_type == "refusal":
                    # Model refused to respond
                    refusal = content_item.get("refusal", "")
                    if refusal:
                        text_parts.append(f"[REFUSAL]: {refusal}")

        elif item_type == "function_call":
            # Modern function/tool call format
            func_call = {
                "id": output_item.get("id"),
                "call_id": output_item.get("call_id"),
                "name": output_item.get("name"),
                "arguments": output_item.get("arguments"),
                "type": "function_call",
            }
            result.function_calls.append(func_call)
            result.tool_calls.append(func_call)

        elif item_type == "web_search_call":
            # Web search action
            action = output_item.get("action") or {}
            web_search = {
                "id": output_item.get("id"),
                "query": action.get("query"),
                "status": output_item.get("status"),
                "type": "web_search_call",
            }
            result.web_search_calls.append(web_search)
            result.tool_calls.append(web_search)

        elif item_type == "reasoning":
            # Accumulate reasoning text parts (consistent with text content handling)
            reasoning_parts: list[str] = []
            reasoning_content = output_item.get("content") or []
            for item in reasoning_content:
                if item.get("type") == "reasoning_text":
                    reasoning_text = item.get("text", "")
                    if reasoning_text:
                        reasoning_parts.append(reasoning_text)
            if reasoning_parts:
                # Append to existing reasoning content if any
                existing = result.reasoning_content or ""
                new_content = "\n".join(reasoning_parts)
                result.reasoning_content = (
                    f"{existing}\n{new_content}".strip() if existing else new_content
                )

        elif item_type == "tool_call":
            # Generic tool call format - normalize to function_call structure
            func = output_item.get("function") or {}
            tool_call = {
                "id": output_item.get("id"),
                "call_id": output_item.get("id"),
                "name": func.get("name"),
                "arguments": func.get("arguments"),
                "type": "function_call",
            }
            result.function_calls.append(tool_call)
            result.tool_calls.append(tool_call)

        else:
            # Log unknown types for debugging
            logger.debug(f"Unknown output item type: {item_type}")

    result.text = "\n".join(text_parts) if text_parts else ""
    return result


def responses_output_to_message(parsed: ParsedResponsesOutput) -> dict[str, Any]:
    """
    Convert parsed Responses API output to a Chat Completions-shaped dict.

    This allows the response to be stored in the trajectory in a format
    compatible with the rest of the loop (upstream returns a litellm
    ``Message``; Atoll's history is plain dicts).
    """
    # Build tool_calls in Chat Completions format
    tool_calls = None
    if parsed.function_calls:
        tool_calls = [
            {
                "id": fc.get("id") or fc.get("call_id") or f"call_{i}",
                "type": "function",
                "function": {
                    "name": fc.get("name"),
                    "arguments": fc.get("arguments") or "{}",
                },
            }
            for i, fc in enumerate(parsed.function_calls)
        ]

    message_data: dict[str, Any] = {
        "role": "assistant",
        "content": parsed.text or None,
    }

    if tool_calls:
        message_data["tool_calls"] = tool_calls

    if parsed.reasoning_content:
        message_data["reasoning_content"] = parsed.reasoning_content

    if reasoning_items := encrypted_reasoning_items(parsed.raw_output_items):
        message_data["reasoning_items"] = reasoning_items

    return message_data
