# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""code_execution_server: Python execution, ported from Archipelago's python_exec.

Model-visible surface (name ``python_code_execution``, parameters, description
wording, and the response fields success/output/stdout/stderr/exit_code/timed_out/
truncated) matches the upstream tool. Execution differs under the hood: upstream
uses a seccomp launcher inside the task container; Atoll runs the code in a
short-lived ``--network none`` Docker container. Model code NEVER runs on
the host.
"""

import os
from typing import Annotated, Any

from atoll_tools.context import get_workspace_root
from atoll_tools.registry import register
from pydantic import BaseModel, ConfigDict, Field

MAX_STREAM_CHARS = 64_000

_DEFAULT_TIMEOUT = 300


def _timeout_seconds() -> int:
    try:
        return int(os.environ.get("CODE_EXEC_COMMAND_TIMEOUT", _DEFAULT_TIMEOUT))
    except ValueError:
        return _DEFAULT_TIMEOUT


def python_tool_description() -> str:
    """The model-visible tool description, assembled like upstream's (limits included)."""
    try:
        from atoll_env.sandbox import load_manifest

        packages = ", ".join(
            f"{n}=={v}" for n, v in sorted(load_manifest()["packages"].items())
        )
    except (OSError, KeyError, ValueError):
        packages = "the preinstalled packages"
    limits = f"Time limit {_timeout_seconds()} seconds per call; memory limit 512 MB"
    return (
        "Run Python 3 code and return its stdout, stderr and exit code. Pass Python source "
        "in `code` (not a shell command). Available: the standard library and "
        f"{packages}. No shell, subprocesses, network access or package installs. Each call "
        "runs in a new process, so variables do not persist between calls; files written to "
        f"the working directory do. {limits}. Print the values you need."
    )


class PythonExecResponse(BaseModel):
    """Response model for python_code_execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(
        ...,
        description="True when the code exited 0 without a timeout or system error.",
    )
    output: str = Field(
        ..., description="stdout, then stderr under 'Stderr output:' when present."
    )
    stdout: str = Field(..., description="Captured stdout (first 64000 characters).")
    stderr: str = Field(..., description="Captured stderr (first 64000 characters).")
    exit_code: int | None = Field(
        ..., description="Process exit code; null when the run did not finish."
    )
    timed_out: bool = Field(
        ..., description="True when the time limit stopped the code."
    )
    truncated: bool = Field(
        ..., description="True when stdout or stderr was cut to 64000 characters."
    )


def _cap(text: str) -> tuple[str, bool]:
    return text[:MAX_STREAM_CHARS], len(text) > MAX_STREAM_CHARS


def _response(
    stdout: str, stderr: str, exit_code: int | None, timed_out: bool, note: str = ""
) -> PythonExecResponse:
    out, out_cut = _cap(stdout)
    err, err_cut = _cap(stderr)
    combined = out
    if err.strip():
        combined = (
            f"{out.rstrip()}\n\nStderr output:\n{err.strip()}"
            if out.strip()
            else err.strip()
        )
    if note:
        combined = f"{combined}\n\n{note}" if combined else note
    return PythonExecResponse(
        success=exit_code == 0 and not timed_out and not note,
        output=combined,
        stdout=out,
        stderr=err,
        exit_code=exit_code,
        timed_out=timed_out,
        truncated=out_cut or err_cut,
    )


@register("code_execution_server")
def python_code_execution(
    code: Annotated[str, Field(description="Python source code to run.")],
) -> PythonExecResponse:
    """Run Python code in a fresh sandboxed process and return stdout, stderr and exit code."""
    from atoll_env.sandbox import ContainerSandbox

    timeout_value = _timeout_seconds()
    result: dict[str, Any] = ContainerSandbox().run_python(
        code, get_workspace_root(), timeout=timeout_value
    )
    if "error" in result:
        return _response("", "", None, False, f"System error: {result['error']}")
    if result["timed_out"]:
        return _response(
            result["stdout"],
            result["stderr"],
            None,
            True,
            f"Execution timed out after {timeout_value} seconds",
        )
    return _response(result["stdout"], result["stderr"], result["exit_code"], False)


# Upstream registers the tool with this dynamic description rather than the docstring.
python_code_execution.__doc__ = python_tool_description()
