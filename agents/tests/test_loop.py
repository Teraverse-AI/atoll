"""Loop tests for the ported LoopTruncatedToolsAgent — scripted LLM, no network."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from runner.loop import (
    CONTEXT_TRIM_NOTICE_TEMPLATE,
    REASONING_ONLY_NUDGE,
    STEP_TOOL_BUDGET_REFUSAL_TEMPLATE,
    TOKEN_BUDGET_EXHAUSTED_TEMPLATE,
    TOKEN_BUDGET_WARNING_TEMPLATE,
    LoopTruncatedToolsAgent,
    _truncate_output,
)
from runner.models import AgentRunInput, AgentStatus
from runner.providers.base import ToolProvider

# ── fakes ────────────────────────────────────────────────────────────────


def _usage(prompt=50, completion=20):
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        completion_tokens_details=None,
        prompt_tokens_details=None,
    )


def _response(content=None, tool_calls=None, prompt_tokens=50, completion_tokens=20,
              reasoning_content=None, empty_choices=False):
    message = SimpleNamespace(
        role="assistant",
        content=content,
        tool_calls=tool_calls,
        reasoning_content=reasoning_content,
        thinking_blocks=None,
    )
    return SimpleNamespace(
        choices=[] if empty_choices else [SimpleNamespace(message=message)],
        usage=_usage(prompt_tokens, completion_tokens),
    )


def _tool_call(call_id, name, args):
    arguments = args if isinstance(args, str) else json.dumps(args)
    return SimpleNamespace(
        id=call_id, type="function", function=SimpleNamespace(name=name, arguments=arguments)
    )


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, model, messages, tools, timeout, extra_args, **kwargs):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        return self.responses.pop(0)


class EchoProvider(ToolProvider):
    def __init__(self):
        self.called_with = []

    def list_tools(self):
        return [{"type": "function", "function": {"name": "echo", "description": "",
                                                  "parameters": {"type": "object",
                                                                 "properties": {},
                                                                 "required": []}}}]

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


def _run_input(workspace_dir="/tmp/x", **config) -> AgentRunInput:
    return AgentRunInput(
        trajectory_id="t1",
        initial_messages=[
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "do the thing"},
        ],
        workspace_dir=workspace_dir,
        orchestrator_model="fake",
        orchestrator_extra_args=None,
        agent_config_values=config,
    )


def _run(agent):
    return asyncio.run(agent.run())


# ── terminal paths ───────────────────────────────────────────────────────


def test_tool_call_then_final_answer_with_usage_accounting():
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {"a": 1})]),
        _response(content="The answer is 42."),
    ])
    provider = EchoProvider()
    output = _run(LoopTruncatedToolsAgent(_run_input(), provider, llm_fn=llm))

    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "The answer is 42."
    assert output.error is None and output.fault is None
    assert provider.called_with == [("echo", {"a": 1})]
    assert output.usage["prompt_tokens"] == 100
    assert output.usage["completion_tokens"] == 40
    assert output.usage["total_tokens"] == 140
    assert len(output.usage["call_log"]) == 2
    # breakdown tracking is on: tool-result tokens land on the step's entry
    assert output.usage["call_log"][0]["tool_call_output_tokens"] > 0

    tool_msg = next(m for m in output.messages if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "c1"
    assert tool_msg["name"] == "echo"
    assert json.loads(tool_msg["content"]) == {"echoed": {"a": 1}}


def test_max_steps_not_finalized_is_failed():
    forever = [_response(tool_calls=[_tool_call(f"c{i}", "echo", {})]) for i in range(5)]
    output = _run(
        LoopTruncatedToolsAgent(_run_input(max_steps=3), EchoProvider(), llm_fn=ScriptedLLM(forever))
    )
    assert output.status == AgentStatus.FAILED
    assert output.error == "Agent loop was not finalized after 3 steps"
    assert output.fault is None
    assert len(output.usage["call_log"]) == 3


def test_wall_clock_timeout_sets_error_and_fault():
    async def slow_llm(model, messages, tools, timeout, extra_args, **kwargs):
        await asyncio.sleep(0.05)
        return _response(content="late")

    agent = LoopTruncatedToolsAgent(_run_input(timeout=0), EchoProvider(), llm_fn=slow_llm)
    output = _run(agent)
    assert output.status == AgentStatus.ERROR
    assert "timed out after 0 seconds" in output.error
    assert output.fault == {"kind": "budget_exhausted", "budget": "wall_clock", "limit": 0}


# ── token budget ─────────────────────────────────────────────────────────


def test_token_budget_warning_then_exhaustion_grants_final_turn():
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})],
                  prompt_tokens=900, completion_tokens=200),
        _response(content="Out of budget, best answer."),
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(token_budget=1000), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "Out of budget, best answer."
    # Step 1 got the remaining-budget warning; step 2 got the exhausted warning.
    first_warning = TOKEN_BUDGET_WARNING_TEMPLATE.format(tokens_remaining=1000, token_budget=1000)
    exhausted = TOKEN_BUDGET_EXHAUSTED_TEMPLATE.format(token_budget=1000, tokens_spent=1100)
    contents = [m.get("content") for m in llm.calls[1]["messages"]]
    assert first_warning in contents
    assert exhausted in contents
    # Upstream keeps the tool list on the final turn.
    assert llm.calls[1]["tools"]
    assert output.usage["token_budget"] == 1000
    assert output.usage["tokens_spent"] == 1170  # 1100 at the warning + the final call's 70
    assert output.usage["accounting_mode"] == "token_accounting"  # back-compat promotion


def test_token_budget_exhausted_without_answer_is_failed():
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})],
                  prompt_tokens=900, completion_tokens=200),
        _response(tool_calls=[_tool_call("c2", "echo", {})]),  # calls tools on the final turn
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(token_budget=1000), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.FAILED
    assert output.error == "Agent loop not finalized after exhausting token budget of 1000"
    assert output.fault is None


# ── empty / reasoning-only replies ───────────────────────────────────────


def test_empty_replies_nudged_then_abandoned():
    llm = ScriptedLLM([_response(empty_choices=True) for _ in range(4)])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.FAILED
    assert "no usable reply" in output.error
    assert output.fault == {"kind": "empty_response"}
    nudges = [m for m in output.messages if m.get("content") == "continue"]
    assert len(nudges) == 3  # 3 nudges, 4th empty reply abandons


def test_reasoning_only_turn_nudged_then_answer():
    llm = ScriptedLLM([
        _response(reasoning_content="thinking hard..."),
        _response(content="done"),
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "done"
    assert any(m.get("content") == REASONING_ONLY_NUDGE for m in output.messages)


def test_reasoning_only_turns_abandoned():
    llm = ScriptedLLM([_response(reasoning_content="loop") for _ in range(4)])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.FAILED
    assert "reasoning with no answer" in output.error
    assert output.fault == {"kind": "empty_response"}


def test_empty_content_without_reasoning_finalizes_no_content():
    # Upstream semantics: a tool-less, reasoning-less turn finalizes, with
    # "No content" standing in for the missing answer.
    llm = ScriptedLLM([_response(content=None)])
    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=llm))
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "No content"


# ── tool execution ───────────────────────────────────────────────────────


def test_malformed_tool_arguments_become_error_observation():
    bad_call = _tool_call("c1", "echo", "{not json")
    llm = ScriptedLLM([_response(tool_calls=[bad_call]), _response(content="done")])
    provider = EchoProvider()
    output = _run(LoopTruncatedToolsAgent(_run_input(), provider, llm_fn=llm))
    assert provider.called_with == []  # never reached the tool
    tool_msg = next(m for m in output.messages if m["role"] == "tool")
    assert tool_msg["content"].startswith("Error executing tool echo:")
    assert output.status == AgentStatus.COMPLETED


def test_tool_call_timeout_becomes_observation():
    class SlowProvider(EchoProvider):
        def call_tool(self, name, arguments):
            time.sleep(0.5)
            return "never seen"

    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})]),
        _response(content="done"),
    ])
    agent = LoopTruncatedToolsAgent(_run_input(tool_call_timeout=0.05), SlowProvider(), llm_fn=llm)
    output = _run(agent)
    tool_msg = next(m for m in output.messages if m["role"] == "tool")
    assert tool_msg["content"] == "Tool call timed out"
    assert output.status == AgentStatus.COMPLETED


class ImageProvider(EchoProvider):
    class _Image:
        mime_type = "image/png"
        base64_data = "aGk="

    def call_tool(self, name, arguments):
        return self._Image()


def test_image_observation_rides_in_followup_user_message():
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})]),
        _response(content="described the image"),
    ])
    output = _run(LoopTruncatedToolsAgent(_run_input(), ImageProvider(), llm_fn=llm))
    assert output.status == AgentStatus.COMPLETED
    tool_msg = next(m for m in output.messages if m["role"] == "tool")
    assert tool_msg["content"] == "Image result attached in the following message."
    image_msg = output.messages[output.messages.index(tool_msg) + 1]
    assert image_msg["role"] == "user"
    assert image_msg["content"][0]["image_url"]["url"] == "data:image/png;base64,aGk="


def test_truncate_output_lines_and_chars():
    text = "\n".join(str(i) for i in range(300))
    out = _truncate_output(text, max_lines=200, max_chars=32768)
    assert "(truncated 100 lines)" in out
    long_line = "x" * 40000
    out = _truncate_output(long_line, max_lines=200, max_chars=32768)
    assert out.startswith("x" * 100)
    assert "(truncated 7232 chars)" in out


# ── context trim ─────────────────────────────────────────────────────────


def test_context_trim_drops_oldest_exchange_and_appends_notice():
    big = "word " * 400  # ~500 estimated tokens per tool result
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {"n": 1})]),
        _response(tool_calls=[_tool_call("c2", "echo", {"n": 2})]),
        _response(content="final"),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(context_trim_utilization=0.5), BigTextProvider(big), llm_fn=llm
    )
    # Force a tiny window so the second exchange pushes the projection over
    # the 50% trim limit before the third dispatch.
    agent._max_input_tokens = 600
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED

    notice = CONTEXT_TRIM_NOTICE_TEMPLATE.format(dropped_turns=1, dropped_messages=2)
    assert any(m.get("content") == notice for m in output.messages)
    assert output.usage["compaction_count"] == 1
    # The oldest exchange (assistant c1 + its tool result) was dropped...
    tool_call_ids = [m.get("tool_call_id") for m in output.messages if m.get("role") == "tool"]
    assert tool_call_ids == ["c2"]
    # ...while the anchors survived.
    assert output.messages[0] == {"role": "system", "content": "sys"}
    assert output.messages[1] == {"role": "user", "content": "do the thing"}


# ── step tool-result budget ──────────────────────────────────────────────


def test_step_tool_result_budget_refusal_with_preview():
    big = "z" * 4000
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})]),
        _response(content="done"),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(max_step_tool_result_tokens=10), BigTextProvider(big), llm_fn=llm
    )
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED

    tool_msg = next(m for m in output.messages if m.get("role") == "tool")
    assert isinstance(tool_msg["content"], list)
    text = tool_msg["content"][0]["text"]
    assert text.startswith("Error: this result was NOT delivered.")
    assert "First 400 characters of the discarded result:\n" + "z" * 400 in text
    # The refusal template wording survived verbatim.
    head = STEP_TOOL_BUDGET_REFUSAL_TEMPLATE.split("{tokens}")[0]
    assert text.startswith(head)
    # None of the payload reached the model beyond the preview.
    assert "z" * 401 not in text


def test_step_tool_result_budget_spills_to_workspace(tmp_path):
    big = "s" * 4000
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})]),
        _response(content="done"),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(
            workspace_dir=str(tmp_path),
            max_step_tool_result_tokens=10,
            spill_oversized_results=True,
        ),
        BigTextProvider(big),
        llm_fn=llm,
    )
    output = _run(agent)
    assert output.status == AgentStatus.COMPLETED

    spill_files = list(tmp_path.glob("spill_001_echo_*.txt"))
    assert len(spill_files) == 1
    assert spill_files[0].read_text() == big  # the ORIGINAL payload, whole

    tool_msg = next(m for m in output.messages if m.get("role") == "tool")
    text = tool_msg["content"][0]["text"]
    assert "The COMPLETE result is at: /" + spill_files[0].name in text
    assert text.startswith("This result was too large to deliver inline")


def test_spill_failure_falls_back_to_refusal(tmp_path):
    missing = tmp_path / "nope"
    llm = ScriptedLLM([
        _response(tool_calls=[_tool_call("c1", "echo", {})]),
        _response(content="done"),
    ])
    agent = LoopTruncatedToolsAgent(
        _run_input(
            workspace_dir=str(missing),
            max_step_tool_result_tokens=10,
            spill_oversized_results=True,
        ),
        BigTextProvider("q" * 4000),
        llm_fn=llm,
    )
    output = _run(agent)
    tool_msg = next(m for m in output.messages if m.get("role") == "tool")
    assert tool_msg["content"][0]["text"].startswith("Error: this result was NOT delivered.")


# ── async llm_fn ─────────────────────────────────────────────────────────


def test_async_llm_fn_supported():
    responses = [_response(content="async answer")]

    async def allm(model, messages, tools, timeout, extra_args, **kwargs):
        return responses.pop(0)

    output = _run(LoopTruncatedToolsAgent(_run_input(), EchoProvider(), llm_fn=allm))
    assert output.status == AgentStatus.COMPLETED
    assert output.final_answer == "async answer"


# ── config coercion smoke ────────────────────────────────────────────────


def test_garbage_config_falls_back_to_defaults():
    agent = LoopTruncatedToolsAgent(
        _run_input(
            token_budget="garbage",
            turn_warnings_enabled="yes-ish",
            accounting_mode="nonsense",
            context_trim_utilization="NaN",
            max_output_lines=None,
            max_output_chars=-5,
        ),
        EchoProvider(),
        llm_fn=ScriptedLLM([]),
    )
    assert agent.token_budget == 0
    assert agent.turn_warnings_enabled is False
    assert agent.accounting_mode == "default"
    assert agent.context_trim_utilization == 0.0
    assert agent.max_output_lines == 200
    assert agent.max_output_chars == 32768


def test_explicit_default_accounting_mode_is_not_repromoted():
    agent = LoopTruncatedToolsAgent(
        _run_input(token_budget=1000, accounting_mode="default"),
        EchoProvider(),
        llm_fn=ScriptedLLM([]),
    )
    assert agent.accounting_mode == "default"
    assert agent.token_budget == 0


@pytest.mark.parametrize("value,expected", [(True, True), ("false", False), (0, False)])
def test_coerce_bool_examples(value, expected):
    from runner.loop import _coerce_bool

    assert _coerce_bool(value, default=True) is expected
