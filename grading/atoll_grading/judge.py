"""The per-item judge: one tool-less LLM call per rubric item.

Prompts live in ``grading/prompts/`` (system prompt + per-item user message, the item
last so everything before it is a per-run prefix the provider can cache). Calls go
through Atoll's vendored LLM layer (``runner.llm.generate_response``) — the same
routing (an OpenAI-compatible endpoint via ``LITELLM_BASE_URL``, or direct to the
provider), request surgery and retry policy as the agent loop.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_JSON = re.compile(r"\{.*\}", re.S)
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
PARSE_ATTEMPTS = 3


class JudgeError(RuntimeError):
    """An item could not be judged (LLM failure or unparseable output after retries)."""


@dataclass
class Prompts:
    system: str
    item: str

    @classmethod
    def load(cls, directory: Path = PROMPTS) -> Prompts:
        return cls((directory / "judge_system.md").read_text(encoding="utf-8"),
                   (directory / "judge_item.md").read_text(encoding="utf-8"))


@dataclass
class ItemResult:
    rubric_number: int
    weight: int
    verdict: int
    rationale: str
    extra: dict[str, Any] = field(default_factory=dict)  # deduction_for, reference_has_error
    usage: dict[str, int] = field(default_factory=dict)


def item_payload(item: dict) -> str:
    keys = ("rubric_number", "rubric_tag", "rubric_weight", "rubric_detail")
    return json.dumps({k: item[k] for k in keys if k in item}, ensure_ascii=False, indent=2)


def build_user_message(template: str, *, task_prompt: str, final_answer: str, candidate_files: str,
                       reference_files: str, related_rows: str, rubric_item: str) -> str:
    values = {"task_prompt": task_prompt, "final_answer": final_answer or "(无最终回复)",
              "candidate_files": candidate_files, "reference_files": reference_files,
              "related_rows": related_rows or "（无）", "rubric_item": rubric_item}
    # One pass over the template only: placeholder-like text inside the (agent-written)
    # blocks is never substituted.
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def parse_verdict(text: str, weight: int) -> tuple[int, str, dict[str, Any]]:
    """(verdict, rationale, extra fields) from the judge's JSON; raises ValueError."""
    match = _JSON.search(text or "")
    if not match:
        raise ValueError(f"no JSON object in judge output: {(text or '')[:200]!r}")
    data = json.loads(match.group(0))
    verdict = data.get("verdict")
    if verdict not in (0, 1) or isinstance(verdict, bool):
        raise ValueError(f"verdict must be 0 or 1, got {verdict!r}")
    extra = {}
    if weight < 0:
        if not isinstance(data.get("deduction_for"), str) or not isinstance(data.get("reference_has_error"), bool):
            raise ValueError("penalty item needs deduction_for (str) and reference_has_error (bool)")
        extra = {"deduction_for": data["deduction_for"], "reference_has_error": data["reference_has_error"]}
    return verdict, str(data.get("rationale", "")), extra


def _usage(response) -> dict[str, int]:
    u = getattr(response, "usage", None)
    if u is None:
        return {}
    get = (lambda k: u.get(k)) if isinstance(u, dict) else (lambda k: getattr(u, k, None))
    return {k: int(get(k) or 0) for k in ("prompt_tokens", "completion_tokens")}


async def judge_item(model: str, prompts: Prompts, user_message: str, item: dict, *,
                     timeout: int, extra_args: dict[str, Any]) -> ItemResult:
    from runner.llm import generate_response

    weight = int(item.get("rubric_weight") or 0)
    messages: list[dict[str, Any]] = [{"role": "system", "content": prompts.system},
                                      {"role": "user", "content": user_message}]
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    last_error = None
    for _ in range(PARSE_ATTEMPTS):
        try:
            response = await generate_response(model=model, messages=messages, tools=[],
                                               llm_response_timeout=timeout, extra_args=extra_args)
        except Exception as exc:  # the vendored layer already retried transient failures
            raise JudgeError(f"item {item.get('rubric_number')}: LLM call failed: {exc}") from exc
        for k, v in _usage(response).items():
            usage[k] += v
        text = response.choices[0].message.content or ""
        try:
            verdict, rationale, extra = parse_verdict(text, weight)
        except (ValueError, json.JSONDecodeError) as exc:
            last_error = exc
            continue
        return ItemResult(int(item.get("rubric_number", 0)), weight, verdict, rationale, extra, usage)
    raise JudgeError(f"item {item.get('rubric_number')}: unparseable judge output: {last_error}")


async def judge_run(model: str, prompts: Prompts, messages: list[tuple[dict, str]], *,
                    concurrency: int, timeout: int, extra_args: dict[str, Any]) -> list[ItemResult]:
    """Judge every (item, user_message) pair; raises JudgeError if any item fails."""
    sem = asyncio.Semaphore(concurrency)

    async def one(item: dict, message: str) -> ItemResult:
        async with sem:
            return await judge_item(model, prompts, message, item, timeout=timeout, extra_args=extra_args)

    return list(await asyncio.gather(*(one(item, msg) for item, msg in messages)))
