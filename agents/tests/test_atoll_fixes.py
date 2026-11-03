"""Atoll-local provider fixes: ARK thinking-budget shaping and empty-reply retries."""

import asyncio
from types import SimpleNamespace

import runner.utils.llm as vendored
from runner.llm import generate_response


def _reply(content, reasoning=None, tool_calls=None):
    message = SimpleNamespace(
        role="assistant", content=content, reasoning_content=reasoning, tool_calls=tool_calls
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )


class VendoredSpy:
    """Replaces the vendored generate_response; records each call's shape."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def __call__(self, model, messages, tools, llm_response_timeout, extra_args, **kw):
        self.calls.append(
            {"timeout": llm_response_timeout, "extra_args": dict(extra_args)}
        )
        return self.replies.pop(0)


def _run(monkeypatch, spy, model, extra_args=None, timeout=600):
    monkeypatch.setattr(vendored, "generate_response", spy)
    return asyncio.run(
        generate_response(model, [{"role": "user", "content": "hi"}], None, timeout, extra_args)
    )


def test_ark_request_shaping(monkeypatch):
    spy = VendoredSpy([_reply("fine")])
    _run(monkeypatch, spy, "volcengine/deepseek-v4", {"max_tokens": 4096})
    call = spy.calls[0]
    assert call["extra_args"]["max_tokens"] == 16384  # raised: thinking shares the budget
    assert call["timeout"] == 900  # thinking takes minutes


def test_ark_absent_max_tokens_left_absent(monkeypatch):
    spy = VendoredSpy([_reply("fine")])
    _run(monkeypatch, spy, "volcengineC/deepseek-v4-flash")
    assert "max_tokens" not in spy.calls[0]["extra_args"]


def test_non_ark_untouched(monkeypatch):
    spy = VendoredSpy([_reply("fine")])
    _run(monkeypatch, spy, "openai/gpt-4o", {"max_tokens": 4096})
    call = spy.calls[0]
    assert call["extra_args"] == {"max_tokens": 4096}
    assert call["timeout"] == 600


def test_thinking_disabled_skips_budget_raise(monkeypatch):
    spy = VendoredSpy([_reply("fine")])
    _run(monkeypatch, spy, "volcengine/deepseek-v4",
         {"max_tokens": 4096, "thinking": {"type": "disabled"}})
    assert spy.calls[0]["extra_args"]["max_tokens"] == 4096


def test_empty_thinking_reply_retried_then_returned(monkeypatch):
    spy = VendoredSpy([
        _reply("", reasoning="x" * 1000),   # budget eaten
        _reply("real answer", reasoning="short"),
    ])
    response = _run(monkeypatch, spy, "volcengine/deepseek-v4", {"max_tokens": 4096})
    assert response.choices[0].message.content == "real answer"
    assert len(spy.calls) == 2
    # retry 1 doubles the (already shaped) budget
    assert spy.calls[1]["extra_args"]["max_tokens"] == 32768


def test_retries_only_escalate_budget_never_config(monkeypatch):
    spy = VendoredSpy([
        _reply("", reasoning="think"),
        _reply("", reasoning="think again"),
        _reply("answer at last"),
    ])
    response = _run(monkeypatch, spy, "volcengine/deepseek-v4",
                    {"max_tokens": 4096, "reasoning_effort": "max"})
    assert response.choices[0].message.content == "answer at last"
    assert len(spy.calls) == 3
    # shaped 16384 -> doubled 32768 -> doubled 65536; configuration untouched
    assert [c["extra_args"]["max_tokens"] for c in spy.calls] == [16384, 32768, 65536]
    for call in spy.calls:
        assert "thinking" not in call["extra_args"]
        assert call["extra_args"]["reasoning_effort"] == "max"


def test_gives_up_after_two_retries(monkeypatch):
    spy = VendoredSpy([_reply("", reasoning="t")] * 3)
    response = _run(monkeypatch, spy, "volcengine/deepseek-v4")
    assert len(spy.calls) == 3
    assert response.choices[0].message.content == ""  # loop handles it from here


def test_tool_call_reply_is_not_retried(monkeypatch):
    tc = [SimpleNamespace(id="c1")]
    spy = VendoredSpy([_reply("", reasoning="t", tool_calls=tc)])
    _run(monkeypatch, spy, "volcengine/deepseek-v4")
    assert len(spy.calls) == 1  # empty content with tool calls is a normal turn


def test_escape_hatch_disables_everything(monkeypatch):
    spy = VendoredSpy([_reply("", reasoning="t")])
    _run(monkeypatch, spy, "volcengine/deepseek-v4",
         {"max_tokens": 4096, "atoll_fixes": False})
    assert len(spy.calls) == 1
    call = spy.calls[0]
    assert call["extra_args"] == {"max_tokens": 4096}  # key stripped, nothing shaped
    assert call["timeout"] == 600
