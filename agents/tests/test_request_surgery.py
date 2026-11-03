"""Provider-specific request surgery, vendored verbatim from upstream.

Runtime-free: ``runner.utils.llm.acompletion`` (the name the vendored module
bound at import) is monkeypatched with a fake that records the kwargs it
receives, so each test inspects the exact wire request the pre-call pipeline
built. No network is touched.
"""

import asyncio

import pytest
import runner.llm as llm_adapter
import runner.utils.llm as vllm
from litellm.types.utils import Choices, Message, ModelResponse

EPHEMERAL = {"type": "ephemeral", "ttl": "5m"}


def _ok_response() -> ModelResponse:
    return ModelResponse(
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=Message(role="assistant", content="ok"),
            )
        ]
    )


@pytest.fixture()
def captured(monkeypatch):
    """Patch the vendored acompletion; yield the dict the fake records into."""
    seen: dict = {}

    async def fake_acompletion(**kwargs):
        seen.clear()
        seen.update(kwargs)
        return _ok_response()

    monkeypatch.setattr(vllm, "acompletion", fake_acompletion)
    # Deterministic routing: direct mode unless a test sets the env itself.
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    return seen


def _generate(model, messages, tools=None, extra_args=None):
    return asyncio.run(
        llm_adapter.generate_response(model, messages, tools, 60, extra_args)
    )


# ── Gemini trailing-user-turn normalization ──────────────────────────────────


def test_gemini_trailing_assistant_gets_continuation_nudge(captured):
    _generate(
        "gemini/gemini-2.5-pro",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "thinking out loud"},
        ],
    )
    sent = captured["messages"]
    assert sent[-1] == {"role": "user", "content": "(Continue.)"}
    assert sent[-2]["role"] == "assistant"


def test_gemini_dangling_tool_calls_get_stub_results(captured):
    _generate(
        "gemini/gemini-2.5-pro",
        [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "ls", "arguments": "{}"},
                    }
                ],
            },
        ],
    )
    sent = captured["messages"]
    assert sent[-1]["role"] == "tool"
    assert sent[-1]["tool_call_id"] == "c1"
    assert sent[-1]["content"] == "(no result: the tool was not executed)"


def test_non_gemini_trailing_assistant_left_alone(captured):
    _generate(
        "openai/gpt-4o",
        [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "prefill"},
        ],
    )
    assert captured["messages"][-1]["role"] == "assistant"


# ── Anthropic empty-text handling ────────────────────────────────────────────


def test_anthropic_empty_text_filled_and_trailing_empty_assistant_dropped(captured):
    _generate(
        "anthropic/claude-sonnet-4-5",
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": ""},
            {"role": "assistant", "content": ""},
        ],
    )
    sent = captured["messages"]
    # The trailing empty assistant prefill is dropped outright...
    assert sent[-1]["role"] == "user"
    # ...and the empty user content is placeholder-filled, not whitespace.
    user = sent[-1]
    content = user["content"]
    if isinstance(content, list):  # cache marking may wrap it into blocks
        assert content[0]["text"] == "[empty]"
    else:
        assert content == "[empty]"


# ── Fireworks tool_choice strip ──────────────────────────────────────────────


def test_fireworks_tool_choice_stripped(captured):
    tool = {
        "type": "function",
        "function": {"name": "f", "parameters": {"type": "object", "properties": {}}},
    }
    _generate(
        "fireworks_ai/accounts/fireworks/models/kimi-k2",
        [{"role": "user", "content": "hi"}],
        tools=[tool],
        extra_args={"tool_choice": "auto"},
    )
    assert "tool_choice" not in captured
    assert "tool_choice" not in (captured.get("extra_body") or {})


def test_non_fireworks_tool_choice_kept(captured):
    _generate(
        "openai/gpt-4o",
        [{"role": "user", "content": "hi"}],
        extra_args={"tool_choice": "auto"},
    )
    assert captured["tool_choice"] == "auto"


# ── Prompt-cache breakpoints ─────────────────────────────────────────────────


def test_cache_control_applied_for_allowlisted_model(captured):
    tool = {
        "type": "function",
        "function": {"name": "f", "parameters": {"type": "object", "properties": {}}},
    }
    _generate(
        "anthropic/claude-sonnet-4-5",
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        tools=[tool],
    )
    sent = captured["messages"]
    assert sent[0]["content"][-1]["cache_control"] == EPHEMERAL  # system prompt
    last_content = sent[-1]["content"]
    assert last_content[-1]["cache_control"] == EPHEMERAL  # rolling last message
    assert captured["tools"][-1]["cache_control"] == EPHEMERAL  # last tool


def test_cache_control_absent_for_other_models(captured):
    tool = {
        "type": "function",
        "function": {"name": "f", "parameters": {"type": "object", "properties": {}}},
    }
    _generate(
        "openai/gpt-4o",
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
        tools=[tool],
    )
    assert "cache_control" not in str(captured["messages"])
    assert all("cache_control" not in t for t in captured["tools"])


# ── _merge_split_tool_choices ────────────────────────────────────────────────


def test_merge_split_tool_choices_merges_text_and_tool_choice():
    tool_msg = Message(role="assistant", content=None)
    tool_msg.tool_calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "f", "arguments": "{}"},
        }
    ]
    response = ModelResponse(
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=Message(role="assistant", content="some text"),
            ),
            Choices(index=1, finish_reason="tool_calls", message=tool_msg),
        ]
    )
    merged = vllm._merge_split_tool_choices(response, "openai/responses/gpt-5")
    assert len(merged.choices) == 1
    only = merged.choices[0]
    assert only.index == 0
    assert only.message.tool_calls
    assert only.message.content == "some text"


def test_merge_split_tool_choices_noop_for_chat_models():
    response = ModelResponse(
        choices=[
            Choices(
                index=0,
                finish_reason="stop",
                message=Message(role="assistant", content="a"),
            ),
            Choices(
                index=1,
                finish_reason="stop",
                message=Message(role="assistant", content="b"),
            ),
        ]
    )
    merged = vllm._merge_split_tool_choices(response, "openai/gpt-4o")
    assert len(merged.choices) == 2


# ── _recover_harmony_final ───────────────────────────────────────────────────


def test_recover_harmony_final_moves_answer_into_content():
    msg = Message(role="assistant", content=None)
    msg.reasoning_content = (
        "planning...<|end|><|start|>assistant"
        "<|channel|>final<|message|>The answer is 42.<|return|>"
    )
    response = ModelResponse(
        choices=[Choices(index=0, finish_reason="stop", message=msg)]
    )
    recovered = vllm._recover_harmony_final(response)
    out = recovered.choices[0].message
    assert out.content == "The answer is 42."
    assert out.reasoning_content == "planning..."


def test_recover_harmony_final_leaves_tool_calls_alone():
    msg = Message(role="assistant", content=None)
    msg.tool_calls = [
        {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    ]
    msg.reasoning_content = "<|channel|>final<|message|>partial<|return|>"
    response = ModelResponse(
        choices=[Choices(index=0, finish_reason="tool_calls", message=msg)]
    )
    recovered = vllm._recover_harmony_final(response)
    assert recovered.choices[0].message.content in (None, "")


# ── Target resolution (direct vs configured endpoint) ────────────────────────


def test_direct_mode_leaves_kwargs_unrouted(captured):
    _generate("openai/gpt-4o", [{"role": "user", "content": "hi"}])
    assert "api_base" not in captured
    assert "api_key" not in captured


def test_env_endpoint_sets_api_base_and_key(captured, monkeypatch):
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")
    _generate("openai/gpt-4o", [{"role": "user", "content": "hi"}])
    assert captured["api_base"] == "http://localhost:4000"
    assert captured["api_key"] == "sk-test"
    # Upstream's proxy branch forwards the call timeout through extra_body.
    assert captured["extra_body"]["timeout"] == 60
