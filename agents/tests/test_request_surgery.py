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
    import runner.model_config as mc

    monkeypatch.setattr(mc, "CONFIG_PATH", mc.CONFIG_PATH.with_name("absent-in-tests.yaml"))
    mc._entries.cache_clear()
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


def test_endpoint_calls_carry_atoll_spend_tags(captured, monkeypatch):
    from runner.utils.decorators import task_id_ctx

    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    token = task_id_ctx.set("q001")
    try:
        asyncio.run(llm_adapter.generate_response(
            "openai/gpt-4o", [{"role": "user", "content": "hi"}], None, 60,
            {"extra_headers": {"x-atoll-trajectory": "caller-wins"}}, trajectory_id="t-1"))
        assert captured["extra_headers"]["x-atoll-task"] == "q001"
        assert captured["extra_headers"]["x-atoll-trajectory"] == "caller-wins"
        assert captured["extra_headers"]["task_id"] == "q001"  # upstream's own tag

        seen = {}

        async def fake_aresponses(**kwargs):
            seen.update(kwargs)
            raise RuntimeError("stop after capture")

        monkeypatch.setattr(vllm, "aresponses", fake_aresponses)
        with pytest.raises(RuntimeError):
            asyncio.run(llm_adapter.call_responses_api(
                "openai/gpt-5", [{"role": "user", "content": "hi"}], None, 60, None,
                trajectory_id="t-2"))
        assert seen["extra_headers"]["x-atoll-task"] == "q001"
        assert seen["extra_headers"]["x-atoll-trajectory"] == "t-2"
    finally:
        task_id_ctx.reset(token)


def test_direct_calls_carry_no_atoll_spend_tags(captured):
    from runner.utils.decorators import task_id_ctx

    token = task_id_ctx.set("q001")
    try:
        asyncio.run(llm_adapter.generate_response(
            "openai/gpt-4o", [{"role": "user", "content": "hi"}], None, 60, None, trajectory_id="t-1"))
    finally:
        task_id_ctx.reset(token)
    assert "extra_headers" not in captured


def test_real_litellm_accepts_tool_calls_without_import_errors(monkeypatch):
    """Through the real litellm (no fake): a tool-carrying call must reach the wire.

    litellm imports its MCP-gateway handler (needs the ``mcp`` package) whenever
    ``tools`` is set; a missing dependency surfaces as an APIConnectionError
    mentioning the module, before any request is sent.
    """
    for var in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://127.0.0.1:9")  # refuses at once
    monkeypatch.setenv("LITELLM_API_KEY", "k")
    tool = {"type": "function", "function": {"name": "f", "description": "d",
                                             "parameters": {"type": "object", "properties": {}}}}
    with pytest.raises(Exception) as info:
        asyncio.run(llm_adapter.generate_response(
            "openai/gpt-4o", [{"role": "user", "content": "hi"}], [tool], 5, None, max_attempts=1))
    assert "No module named" not in str(info.value)
    assert "Connection error" in str(info.value)


# ── Routing: endpoint vs direct (ATOLL_DIRECT_MODELS), per-model settings ───


@pytest.fixture()
def model_config(monkeypatch, tmp_path):
    """Write a temporary runner/model_config.yaml; yield a function taking its YAML text."""
    import runner.model_config as mc

    def write(text: str) -> None:
        path = tmp_path / "model_config.yaml"
        path.write_text(text)
        monkeypatch.setattr(mc, "CONFIG_PATH", path)
        mc._entries.cache_clear()

    yield write
    mc._entries.cache_clear()


def test_endpoint_calls_are_marked_per_call(captured, monkeypatch):
    import litellm

    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    _generate("team/model-a", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "litellm_proxy/team/model-a"
    assert litellm.use_litellm_proxy is False  # never the global switch
    _generate("litellm_proxy/team/judge", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "litellm_proxy/team/judge"  # not doubled


def test_direct_models_bypass_the_endpoint(captured, monkeypatch):
    from runner.utils.decorators import task_id_ctx

    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("ATOLL_DIRECT_MODELS", " provider-x/ , provider-y/")
    token = task_id_ctx.set("q001")
    try:
        asyncio.run(llm_adapter.generate_response(
            "provider-x/model-b", [{"role": "user", "content": "hi"}],
            None, 60, None, trajectory_id="t-1"))
    finally:
        task_id_ctx.reset(token)
    assert captured["model"] == "provider-x/model-b"
    assert "api_base" not in captured and "api_key" not in captured
    assert "extra_headers" not in captured  # no spend tags to a provider
    _generate("team/model-a", [{"role": "user", "content": "hi"}])
    assert captured["api_base"] == "http://localhost:4000"


_CONFIG = """
model_list:
  - model_name: short-name
    litellm_params:
      model: openai/provider-model-id
      api_key: os.environ/TEST_PLAN_KEY
      api_base: https://plan.example/v1
      extra_body: {thinking: {type: enabled}, reasoning_effort: max}
      extra_headers: {x-direct: fixed}
  - model_name: team/header-model
    litellm_params:
      extra_headers: {x-provider-key: os.environ/TEST_HEADER_KEY}
"""


def test_config_direct_entry(captured, model_config, monkeypatch):
    model_config(_CONFIG)
    monkeypatch.setenv("TEST_PLAN_KEY", "plan-key")
    _generate("short-name", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "openai/provider-model-id"
    assert captured["api_key"] == "plan-key"
    assert captured["base_url"] == "https://plan.example/v1"
    assert "api_base" not in captured  # base_url: no endpoint spend tags on a provider call
    assert captured["extra_body"]["thinking"] == {"type": "enabled"}
    assert captured["extra_body"]["reasoning_effort"] == "max"
    assert captured["extra_headers"] == {"x-direct": "fixed"}

    _generate("short-name", [{"role": "user", "content": "hi"}],
              extra_args={"extra_body": {"reasoning_effort": "low"}})
    assert captured["extra_body"]["reasoning_effort"] == "low"  # caller wins


def test_config_unset_key_skips_key_and_base(captured, model_config, monkeypatch):
    model_config(_CONFIG)
    monkeypatch.delenv("TEST_PLAN_KEY", raising=False)
    _generate("short-name", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "openai/provider-model-id"
    assert "api_key" not in captured and "base_url" not in captured  # provider defaults


def test_config_endpoint_calls_get_headers_only(captured, model_config, monkeypatch):
    model_config(_CONFIG)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("TEST_HEADER_KEY", "h-key")
    _generate("team/header-model", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "litellm_proxy/team/header-model"
    assert captured["extra_headers"]["x-provider-key"] == "h-key"
    _generate("short-name", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "litellm_proxy/short-name"  # the endpoint resolves it
    assert "extra_body" not in captured or "reasoning_effort" not in captured["extra_body"]
    monkeypatch.delenv("TEST_HEADER_KEY")
    _generate("team/header-model", [{"role": "user", "content": "hi"}])
    assert "x-provider-key" not in captured.get("extra_headers", {})


def test_no_config_file_passes_everything_through(captured, model_config, tmp_path, monkeypatch):
    import runner.model_config as mc

    monkeypatch.setattr(mc, "CONFIG_PATH", tmp_path / "absent.yaml")
    mc._entries.cache_clear()
    _generate("short-name", [{"role": "user", "content": "hi"}])
    assert captured["model"] == "short-name"
