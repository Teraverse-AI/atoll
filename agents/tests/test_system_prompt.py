"""System prompt resolution: per-task > global (CLI, then env) > atoll_agents.md."""

import hashlib
from pathlib import Path

import pytest
from runner.main import (
    APEX_AGENTS_SYSTEM_PROMPT_FILE,
    DEFAULT_SYSTEM_PROMPT_FILE,
    resolve_system_prompt,
)

# sha256 of Mercor's APEX-Agents system prompt (paper Appendix D.2), stripped.
# apex_agents.md is a verbatim reference: it must never change.
APEX_AGENTS_SHA256 = "2e1a79593978c7808c43889610ec5abc75f1ede4e136b4af19ad62fac65c3e0a"


def test_apex_reference_is_mercors_prompt_verbatim():
    text = APEX_AGENTS_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    assert hashlib.sha256(text.encode()).hexdigest() == APEX_AGENTS_SHA256
    assert text.startswith("You are an agent that completes tasks independently.")
    assert "/filesystem/ as the root path" in text


def test_default_is_the_editable_atoll_prompt():
    assert DEFAULT_SYSTEM_PROMPT_FILE.name == "atoll_agents.md"
    assert DEFAULT_SYSTEM_PROMPT_FILE != APEX_AGENTS_SYSTEM_PROMPT_FILE
    assert DEFAULT_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()  # non-empty


def test_default_when_nothing_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("ATOLL_SYSTEM_PROMPT_FILE", raising=False)
    text, source = resolve_system_prompt(tmp_path, {}, None)
    assert text == DEFAULT_SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    assert source.startswith("default")


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_global_env_then_cli(tmp_path, monkeypatch):
    env_file = _write(tmp_path / "env.md", "from env\n")
    cli_file = _write(tmp_path / "cli.md", "  from cli  ")
    monkeypatch.setenv("ATOLL_SYSTEM_PROMPT_FILE", str(env_file))
    assert resolve_system_prompt(tmp_path, {}, None)[0] == "from env"
    assert resolve_system_prompt(tmp_path, {}, cli_file)[0] == "from cli"  # CLI > env


def test_per_task_beats_global(tmp_path, monkeypatch):
    _write(tmp_path / "system_prompt.md", "task prompt")
    monkeypatch.setenv("ATOLL_SYSTEM_PROMPT_FILE", str(_write(tmp_path / "env.md", "env")))
    text, source = resolve_system_prompt(
        tmp_path, {"system_prompt_file": "system_prompt.md"}, _write(tmp_path / "c.md", "cli")
    )
    assert (text, source.split(":")[0]) == ("task prompt", "task")


def test_missing_file_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("ATOLL_SYSTEM_PROMPT_FILE", raising=False)
    with pytest.raises(FileNotFoundError, match="task"):
        resolve_system_prompt(tmp_path, {"system_prompt_file": "nope.md"}, None)
    with pytest.raises(FileNotFoundError, match="global"):
        resolve_system_prompt(tmp_path, {}, tmp_path / "nope.md")
