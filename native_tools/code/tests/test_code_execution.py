import functools
import shutil

import pytest
from atoll_env.sandbox import ContainerSandbox, detect_runtime
from code_execution_server.python_exec import python_code_execution


@functools.cache
def runtime_works() -> bool:
    """A runtime on PATH is not enough (Windows CI has docker without Linux
    containers); probe with a real trivial run once per session."""
    if detect_runtime() is None:
        return False
    result = ContainerSandbox().run_python("print('ok')", ".", timeout=60)
    return result.get("exit_code") == 0


has_runtime = runtime_works()


def test_no_runtime_refuses_instead_of_running_on_host(workspace, monkeypatch):
    """With no container runtime, python_code_execution must error — never exec on the host."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    result = python_code_execution("print('x')")
    assert result.success is False
    assert "Docker not found" in result.output


@pytest.mark.skipif(not has_runtime, reason="no working Docker sandbox on this machine")
def test_sandboxed_execution(workspace):
    (workspace / "data.txt").write_text("42")
    result = python_code_execution("print(int(open('data.txt').read()) * 2)")
    assert result.success is True, result.output
    assert result.stdout.strip() == "84"
    assert result.exit_code == 0
    assert result.timed_out is False


@pytest.mark.skipif(not has_runtime, reason="no working Docker sandbox on this machine")
def test_sandbox_has_no_network(workspace):
    code = (
        "import socket\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 80), timeout=3)\n"
        "    print('CONNECTED')\n"
        "except OSError:\n"
        "    print('NO-NETWORK')\n"
    )
    result = python_code_execution(code)
    assert result.stdout.strip() == "NO-NETWORK"


@pytest.mark.skipif(not has_runtime, reason="no working Docker sandbox on this machine")
def test_stderr_goes_under_heading(workspace):
    result = python_code_execution("import sys; print('out'); print('err', file=sys.stderr)")
    assert "Stderr output:" in result.output
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"
