"""Responses-API path tests for the ported LoopTruncatedToolsAgent — scripted, no network."""

import asyncio
import json

from runner.loop import (
    CONTEXT_TRIM_NOTICE_TEMPLATE,
    REASONING_ONLY_NUDGE,
    LoopTruncatedToolsAgent,
    NoToolCallsTurnMixin,
)
from runner.models import AgentRunInput, AgentStatus
from runner.providers.base import ToolProvider
from runner.responses import to_responses_tool

# ── fakes ────────────────────────────────────────────────────────────────


def _resp(output, input_tokens=50, output_tokens=20, reasoning_tokens=0, cached_tokens=0):
    """A Responses-API-shaped response as a plain dict."""
    return {
        "output": output,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
            "input_tokens_details": {"cached_tokens": cached_tokens},
        },
    }


def _message_item(text):
    return {
        "type": "message",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_call_item(call_id, name, args):
    return {
        "type": "function_call",
        "id": call_id,
        "call_id": call_id,
        "name": name,
        "arguments": args if isinstance(args, str) else json.dumps(args),
    }


def _reasoning_item(encrypted="enc-1", summary=None):
    return {
        "type": "reasoning",
        "id": "rs_1",
        "encrypted_content": encrypted,
        "summary": [{"type": "summary_text", "text": summary}] if summary else [],
        "content": [],
    }


class ScriptedResponses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, model, input_items, tools, timeout, extra_args, **kwargs):
        self.calls.append(
            {"model": model, "input": list(input_items), "tools": tools, "kwargs": kwargs}
        )
        return self.responses.pop(0)


class ScriptedChat:
    """Chat-path sentinel: records calls, never returns a reply."""

    def __init__(self):
        self.calls = []

    def __call__(self, model, messages, tools, timeout, extra_args, **kwargs):
        self.calls.append({"model": model})
        from types import SimpleNamespace

        message = SimpleNamespace(
            role="assistant",
            content="chat answer",
            tool_calls=None,
            reasoning_content=None,
            thinking_blocks=None,
            reasoning_items=None,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=5,
                completion_tokens_details=None,
                prompt_tokens_details=None,
            ),
        )


class EchoProvider(ToolProvider):
    def __init__(self):
        self.called_with = []

    def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "echo",
                    "description": "",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ]

    def call_tool(self, name, arguments):
        self.called_with.append((name, arguments))
        return {"echoed": arguments}


class BigTextProvider(EchoProvider):
    def __init__(self, text):
        super().__init__()
        self.text = text

    def call_tool(self, name, arguments):
        self.called_with.append((name, arguments))
        return self.text


def _run_input(model="openai/responses/gpt-x", **config) -> AgentRunInput:
    return AgentRunInput(
        trajectory_id="t1",
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do the thing"},
        ],
        workspace_dir="/tmp/x",
        orchestrator_model=model,
        orchestrator_extra_args=None,
        agent_config_values=config,
    )


def _run(agent):
    return asyncio.run(agent.run())


# ── gating ───────────────────────────────────────────────────────────────


def test_responses_prefix_selects_responses_path_with_stripped_wire_model():
    chat = ScriptedChat()
    responses = ScriptedResponses([_resp([_message_item("done")])])
    agent = LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=chat, responses_fn=responses)
    assert agent._use_responses is True
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED
    assert chat.calls == []
    # The "responses/" route segment is stripped from the wire model.
    assert responses.calls[0]["model"] == "openai/gpt-x"


def test_plain_model_keeps_chat_path():
    chat = ScriptedChat()
    responses = ScriptedResponses([])
    agent = LoopTruncatedToolsAgent(
        _run_input(model="fake"), EchoProvider(), llm_fn=chat, responses_fn=responses
    )
    assert agent._use_responses is False
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "chat answer"
    assert responses.calls == []


def test_litellm_routes_responses_sends_prefixed_model_over_chat():
    # The configured OpenAI-compatible endpoint (e.g. a LiteLLM proxy) resolves
    # the prefixed model group to the Responses API server-side, so the client
    # stays on Chat Completions and sends the name whole.
    chat = ScriptedChat()
    responses = ScriptedResponses([])
    agent = LoopTruncatedToolsAgent(
        _run_input(litellm_routes_responses=True),
        EchoProvider(),
        llm_fn=chat,
        responses_fn=responses,
    )
    assert agent._use_responses is False
    _run(agent)
    assert responses.calls == []
    assert chat.calls[0]["model"] == "openai/responses/gpt-x"
    # Pricing/window lookups still use the stripped name.
    assert agent._api_model == "openai/gpt-x"


# ── reasoning + tool-call roundtrip ──────────────────────────────────────


def test_reasoning_tool_call_roundtrip_replays_reasoning_items():
    responses = ScriptedResponses([
        _resp([
            _reasoning_item(encrypted="enc-1", summary="planning the call"),
            _function_call_item("c1", "echo", {"a": 1}),
        ]),
        _resp([_message_item("The answer is 42.")]),
    ])
    provider = EchoProvider()
    agent = LoopTruncatedToolsAgent(
        _run_input(replay_reasoning=True), provider, responses_fn=responses
    )
    output = _run(agent)

    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "The answer is 42."
    assert provider.called_with == [("echo", {"a": 1})]
    assert responses.calls[0]["kwargs"]["keep_encrypted_reasoning"] is True

    # The stored assistant message keeps the reasoning for replay, plus the
    # summary-extracted reasoning_content.
    assistant = next(m for m in output.messages if m.get("role") == "assistant")
    assert assistant["reasoning_items"][0]["encrypted_content"] == "enc-1"
    assert assistant["reasoning_content"] == "planning the call"

    # The next request replays the reasoning item ahead of the function_call,
    # and carries the tool result as function_call_output.
    second_input = responses.calls[1]["input"]
    types = [item.get("type") for item in second_input if isinstance(item, dict)]
    assert "reasoning" in types
    replayed = next(i for i in second_input if i.get("type") == "reasoning")
    assert replayed["encrypted_content"] == "enc-1"
    assert types.index("reasoning") < types.index("function_call")
    fn_call = next(i for i in second_input if i.get("type") == "function_call")
    assert fn_call["call_id"] == "c1" and fn_call["name"] == "echo"
    fn_out = next(i for i in second_input if i.get("type") == "function_call_output")
    assert fn_out["call_id"] == "c1"
    assert json.loads(fn_out["output"]) == {"echoed": {"a": 1}}
    # system -> developer on the Responses wire.
    assert second_input[0] == {"role": "developer", "content": "sys"}


def test_replay_off_strips_encrypted_reasoning_from_stored_message():
    responses = ScriptedResponses([
        _resp([
            _reasoning_item(encrypted="enc-1"),
            _function_call_item("c1", "echo", {}),
        ]),
        _resp([_message_item("done")]),
    ])
    agent = LoopTruncatedToolsAgent(_run_input(), EchoProvider(), responses_fn=responses)
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED
    assert responses.calls[0]["kwargs"]["keep_encrypted_reasoning"] is False
    assistant = next(m for m in output.messages if m.get("role") == "assistant")
    assert "reasoning_items" not in assistant
    assert not any(i.get("type") == "reasoning" for i in responses.calls[1]["input"])


# ── reasoning-only turns ─────────────────────────────────────────────────


def test_turn_carried_reasoning_counts_reasoning_items():
    msg = {"role": "assistant", "content": "", "reasoning_items": [{"type": "reasoning"}]}
    assert LoopTruncatedToolsAgent._turn_carried_reasoning(msg) is True
    assert NoToolCallsTurnMixin._turn_carried_reasoning(msg) is False


def test_encrypted_reasoning_only_turn_is_nudged_then_answers():
    responses = ScriptedResponses([
        _resp([_reasoning_item(encrypted="enc-1")]),  # no text, no tool call
        _resp([_message_item("done")]),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(replay_reasoning=True), EchoProvider(), responses_fn=responses
    )
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "done"
    assert any(m.get("content") == REASONING_ONLY_NUDGE for m in output.messages)


def test_reasoning_only_without_replay_maps_to_empty_reply_nudge():
    # With replay off the encrypted item is scrubbed, so the turn maps to
    # nothing and takes the empty-reply "continue" path, not the reasoning
    # nudge — parity with upstream's unusable-reply handling.
    responses = ScriptedResponses([
        _resp([_reasoning_item(encrypted="enc-1")]),
        _resp([_message_item("done")]),
    ])
    agent = LoopTruncatedToolsAgent(_run_input(), EchoProvider(), responses_fn=responses)
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED
    assert any(m.get("content") == "continue" for m in output.messages)
    assert not any(m.get("content") == REASONING_ONLY_NUDGE for m in output.messages)
    # The unmapped reply carried raw output items, so an eventual abandon
    # would be a harness fault with no empty_response kind.
    assert agent._empty_reply_had_output is False  # reset by the usable reply


# ── final answer + usage ─────────────────────────────────────────────────


def test_final_answer_parsed_from_message_items():
    responses = ScriptedResponses([
        _resp([_message_item("Part one."), _message_item("Part two.")]),
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), responses_fn=responses))
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "Part one.\nPart two."


def test_usage_tracked_from_responses_shaped_payload():
    responses = ScriptedResponses([
        _resp(
            [_function_call_item("c1", "echo", {})],
            input_tokens=100,
            output_tokens=30,
            reasoning_tokens=12,
            cached_tokens=40,
        ),
        _resp([_message_item("done")], input_tokens=200, output_tokens=10),
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), responses_fn=responses))
    assert output.status == AgentStatus.COMPLETED
    usage = output.usage
    assert usage["prompt_tokens"] == 300
    assert usage["completion_tokens"] == 40
    assert usage["total_tokens"] == 340
    assert usage["reasoning_tokens"] == 12
    assert usage["cached_tokens"] == 40
    assert usage["max_prompt_tokens"] == 200
    assert len(usage["call_log"]) == 2
    assert usage["call_log"][0]["prompt_tokens"] == 100
    # Breakdown tracking is on: tool-result tokens land on the step's entry.
    assert usage["call_log"][0]["tool_call_output_tokens"] > 0
    assert usage["call_log"][0]["tool_call_input_tokens"] > 0


# ── tool schema conversion ───────────────────────────────────────────────


def test_tools_converted_to_responses_shape():
    responses = ScriptedResponses([_resp([_message_item("done")])])
    _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), responses_fn=responses))
    assert responses.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "echo",
            "strict": False,
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    ]


def test_to_responses_tool_keeps_description_and_strict():
    tool = {
        "type": "function",
        "function": {"name": "t", "description": "desc", "parameters": {"type": "object"}, "strict": True},
    }
    assert to_responses_tool(tool) == {
        "type": "function",
        "name": "t",
        "strict": True,
        "description": "desc",
        "parameters": {"type": "object"},
    }


# ── context trim parity ──────────────────────────────────────────────────


def test_context_trim_fires_on_responses_path():
    big = "word " * 400  # ~500 estimated tokens per tool result
    responses = ScriptedResponses([
        _resp([_function_call_item("c1", "echo", {"n": 1})]),
        _resp([_function_call_item("c2", "echo", {"n": 2})]),
        _resp([_message_item("final")]),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(context_trim_utilization=0.5), BigTextProvider(big), responses_fn=responses
    )
    agent._max_input_tokens = 600
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED

    notice = CONTEXT_TRIM_NOTICE_TEMPLATE.format(dropped_turns=1, dropped_messages=2)
    assert any(m.get("content") == notice for m in output.messages)
    assert output.usage["compaction_count"] == 1
    tool_call_ids = [m.get("tool_call_id") for m in output.messages if m.get("role") == "tool"]
    assert tool_call_ids == ["c2"]
    assert output.messages[0] == {"role": "system", "content": "sys"}


# ── config coercion ──────────────────────────────────────────────────────


def test_replay_reasoning_coercion():
    on = LoopTruncatedToolsAgent(
        _run_input(replay_reasoning="true"), EchoProvider(), responses_fn=ScriptedResponses([])
    )
    assert on.replay_reasoning is True
    off = LoopTruncatedToolsAgent(
        _run_input(replay_reasoning="garbage"), EchoProvider(), responses_fn=ScriptedResponses([])
    )
    assert off.replay_reasoning is False
