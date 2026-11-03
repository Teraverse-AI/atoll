import shutil

import pytest
from atoll_env.sandbox import ContainerSandbox, detect_runtime
from code_execution_server.code_exec import (
    MAX_OUTPUT_SIZE,
    MAX_TRAILING_TEXT,
    CodeExecRequest,
    _binary_format,
    _looks_like_html,
    _sanitize_output,
    _split_binary_prefix,
    code_exec,
)


def _runtime_works() -> bool:
    """A runtime on PATH is not enough: the sandbox image must exist and run."""
    if detect_runtime() is None:
        return False
    return ContainerSandbox().run_shell("echo ok", ".", timeout=60).get("exit_code") == 0


has_runtime = _runtime_works()


# --- safety: never run on the host ---


def test_no_runtime_refuses_instead_of_running_on_host(workspace, monkeypatch):
    """With no container runtime, code_exec must error — never exec on the host."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = code_exec(code="echo should-not-run")
    assert result.success is False
    assert "Docker not found" in result.output


# --- argument handling (no container needed) ---


def test_missing_code_is_rejected(workspace):
    result = code_exec()
    assert result.success is False
    assert "Required parameter 'code'" in result.output


def test_raw_python_is_rejected_with_help(workspace):
    result = code_exec(code="import pandas\nprint(pandas.__version__)")
    assert result.success is False
    assert "raw Python code" in result.output


def test_request_wrapper_recovers_truncated_json_string():
    request = CodeExecRequest.model_validate('{"code": "echo hi')
    assert request.code == "echo hi"


def test_invalid_timeout_env_is_a_configuration_error(workspace, monkeypatch):
    monkeypatch.setenv("CODE_EXEC_COMMAND_TIMEOUT", "soon")
    result = code_exec(code="echo hi")
    assert result.success is False
    assert "Configuration error: Invalid timeout value: soon" in result.output


# --- output-processing heuristics (pure functions) ---


def test_looks_like_html():
    assert _looks_like_html("<!DOCTYPE html><html><body>x</body></html>") is True
    assert _looks_like_html("plain text with a < sign") is False


def test_binary_format_magic_and_density():
    assert _binary_format("PK\x03\x04" + "x" * 50) == "a ZIP archive (.zip, .xlsx, .docx, .pptx)"
    assert _binary_format("%PDF-1.7 blob") == "a PDF document"
    assert _binary_format("has a \x00 nul byte") == "binary data"
    assert _binary_format("�" * 40 + "text" * 10) == "binary data"
    assert _binary_format("ordinary readable output") is None
    assert _binary_format("") is None


def test_split_binary_prefix_keeps_trailing_text():
    dump = "�" * 100
    tail = "file.txt\nREADME.md\n"
    suppressed, kept, dropped = _split_binary_prefix(dump + tail)
    assert suppressed == dump
    assert kept == tail
    assert dropped == 0


def test_split_binary_prefix_bounds_long_tails():
    dump = "\x00" * 100
    tail = "line\n" * 2000  # 10,000 chars of readable trailer
    suppressed, kept, dropped = _split_binary_prefix(dump + tail)
    assert suppressed == dump
    assert len(kept) <= MAX_TRAILING_TEXT
    assert dropped == len(tail) - len(kept)


def test_split_binary_prefix_no_marker_means_no_cut():
    text = "%PDF-1.4 pure ascii pdf header with no undecodable bytes"
    assert _split_binary_prefix(text) == (text, "", 0)


def test_sanitize_output_truncates_with_head_and_tail():
    text = "a" * (MAX_OUTPUT_SIZE + 1000)
    out = _sanitize_output(text)
    assert len(out) < len(text)
    assert "[output truncated" in out
    assert out.startswith("a")
    assert out.endswith("a")


def test_sanitize_output_html_gets_the_small_budget():
    text = "<!DOCTYPE html><body>" + "x" * 10_000 + "</body>"
    out = _sanitize_output(text)
    assert "[output truncated" in out
    assert len(out) < 3_000


def test_sanitize_output_leaves_small_output_alone():
    assert _sanitize_output("hello\n") == "hello\n"


# --- container roundtrips ---


@pytest.mark.skipif(not has_runtime, reason="no working sandbox (runtime or atoll-sandbox image missing)")
def test_echo_roundtrip(workspace):
    result = code_exec(code="echo hello")
    assert result.success is True, result.output
    assert result.output.strip() == "hello"


@pytest.mark.skipif(not has_runtime, reason="no working sandbox (runtime or atoll-sandbox image missing)")
def test_request_wrapper_roundtrip(workspace):
    result = code_exec(request={"code": "echo wrapped"})
    assert result.success is True, result.output
    assert result.output.strip() == "wrapped"


@pytest.mark.skipif(not has_runtime, reason="no working sandbox (runtime or atoll-sandbox image missing)")
def test_nonzero_exit_code_reported(workspace):
    result = code_exec(code="echo partial && exit 3")
    assert result.success is False
    assert "Command failed with exit code 3" in result.output
    assert "partial" in result.output


@pytest.mark.skipif(not has_runtime, reason="no working sandbox (runtime or atoll-sandbox image missing)")
def test_stderr_goes_under_heading(workspace):
    result = code_exec(code="echo out; echo err >&2")
    assert result.success is True, result.output
    assert "Stderr output:" in result.output
    assert "out" in result.output
    assert "err" in result.output


@pytest.mark.skipif(not has_runtime, reason="no working sandbox (runtime or atoll-sandbox image missing)")
def test_workspace_files_visible_and_writable(workspace):
    (workspace / "in.txt").write_text("42")
    result = code_exec(code="cat in.txt > out.txt && cat out.txt")
    assert result.success is True, result.output
    assert result.output.strip() == "42"
    assert (workspace / "out.txt").read_text() == "42"
