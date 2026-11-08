# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""code_execution_server: shell execution, ported from Archipelago's code_exec.

Model-visible surface (name ``code_exec``, the ``request``/``code`` parameters and
their descriptions, the CodeExecResponse fields, and every output-processing
heuristic and notice — truncation, HTML suppression, binary-dump suppression,
the raw-Python safety net) matches the upstream tool verbatim. Execution differs
under the hood:

- Upstream runs the command through an LD_PRELOAD/seccomp sandbox inside the task
  container; Atoll runs it in a short-lived ``--network none`` Docker
  container (python:3.12-slim) via ContainerSandbox.run_shell. Model code NEVER
  runs on the host.
- The working directory is the task workspace mounted at /filesystem — upstream's
  APP_FS_ROOT default — so the description's "/filesystem" and absolute paths a
  model derives from it (e.g. from Mercor's system prompt) resolve as upstream.
- python:3.12-slim provides sh and python3 only — no node or other runtimes an
  upstream deployment may have carried — and the container has no network, so the
  description's `pip install` example will fail at download time.
- /proc and /sys are mounted inside the container (contained, so harmless),
  though slim ships no ps/top/pgrep; the description is kept verbatim.
"""

import dataclasses
import json
import os
import re
from typing import Annotated, Any

from atoll_tools.context import get_workspace_root
from atoll_tools.registry import register
from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_OUTPUT_SIZE = 100_000  # 100KB general limit
MAX_HTML_OUTPUT_SIZE = 2_000  # 2KB for HTML content
TRUNCATION_TAIL_FRACTION = 4  # share of the budget reserved for the tail
BINARY_SCAN_SIZE = 4_096  # chars of a stream inspected for binary markers
# Share of replacement characters that marks a stream as a byte dump rather
# than text. Set well above what a mis-encoded document produces (a latin-1
# page of prose lands a few percent) and well below an actual byte stream,
# which is typically half replacement characters or more.
BINARY_REPLACEMENT_RATIO = 0.3
# Readable text kept after a suppressed byte dump. Covers the diagnostic
# `echo`/`ls` that commonly follows a binary head in the same call, without
# letting a document's own ASCII structure consume the output budget.
MAX_TRAILING_TEXT = 2_000
_HTML_PATTERN = re.compile(
    r"<(!DOCTYPE|html|head|body|div|script|style)\b", re.IGNORECASE
)
# Magic-number prefixes, written as they arrive here rather than as raw bytes:
# output is already decoded with errors="replace" by the time the tool sees it,
# so any header byte >= 0x80 has become U+FFFD and only all-ASCII magic is still
# matchable. These are the formats the reported dumps carried, and naming them
# lets the notice say what the stream was. Everything else is caught by the NUL
# and replacement-density checks below and gets the generic description.
_BINARY_MAGIC: tuple[tuple[str, str], ...] = (
    ("PK\x03\x04", "a ZIP archive (.zip, .xlsx, .docx, .pptx)"),
    ("PK\x05\x06", "an empty ZIP archive"),
    ("%PDF-", "a PDF document"),
)

_STRING_REQUEST_ERROR = (
    "request must be a JSON object, or a JSON string encoding an object "
    "with a 'code' field"
)

CODE_DESCRIPTION = (
    "Shell command to execute in bash (NOT a Python interpreter). "
    "Pass it in exactly one of `code` or `request.code`; a call carrying neither is rejected. "
    "Commands run in the sandbox root directory (/filesystem) with a default timeout of 300 seconds. "
    "Examples: "
    "(1) Simple Python one-liner: `python -c 'print(1+1)'` "
    "(2) Multi-line Python script: `cat > script.py << 'EOF'\\nimport pandas\\nprint(pandas.__version__)\\nEOF && python script.py` "
    "(3) Shell commands: `ls -la`, `echo hello`, `pip install requests` "
    "(4) File operations: `mkdir mydir && touch mydir/file.txt`. "
    "Pass an empty string to execute a no-op shell command."
)


def _parse_string_request(value: str) -> dict[str, Any]:
    """Decode JSON-string requests, including the legacy truncated wrapper.

    Some model tool calls serialize ``request`` as a string containing the
    beginning of ``{"code": "..."}``, but omit the final quote and brace.
    Try the valid form first, then only the two possible missing delimiters.
    ``strict=False`` also recovers literal newlines observed in these wrappers.
    """
    for suffix in ("", '"}', "}"):
        try:
            parsed = json.loads(value + suffix, strict=False)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(_STRING_REQUEST_ERROR)


class CodeExecRequest(BaseModel):
    """Alternate wrapper form. Prefer the top-level `code` parameter."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def parse_string_request(cls, value: Any) -> Any:
        """Accept object-shaped requests serialized into a string."""
        if isinstance(value, str):
            return _parse_string_request(value)
        return value

    code: str | None = Field(None, description=CODE_DESCRIPTION)


class CodeExecResponse(BaseModel):
    """Response model for code execution."""

    model_config = ConfigDict(extra="forbid")

    success: bool = Field(
        ...,
        description=(
            "Boolean indicating successful execution. Returns `true` if the command completed with exit code 0, "
            "did not timeout, and no system errors occurred. Returns `false` if: "
            "(1) exit code was non-zero, "
            "(2) command timed out (default: 300s), "
            "(3) system error occurred (e.g., sandbox unavailable, working directory missing), or "
            "(4) raw Python code was passed without using `python -c` wrapper."
        ),
    )
    output: str = Field(
        ...,
        description=(
            "Combined output from command execution. On success: contains stdout, "
            "plus any stderr output (warnings, logs) under 'Stderr output:'. "
            "On failure: contains error details including stderr. "
            "Large outputs (over 100KB, or 2KB for HTML content) are automatically truncated."
        ),
    )


@dataclasses.dataclass
class SandboxResult:
    """Result of a sandboxed command execution (upstream utils.sandbox contract)."""

    stdout: str
    stderr: str
    return_code: int
    timed_out: bool = False
    error: str | None = None


def _looks_like_html(text: str) -> bool:
    """Detect if output looks like raw HTML."""
    return bool(_HTML_PATTERN.search(text[:1000]))


def _binary_format(text: str) -> str | None:
    """Describe ``text`` when it is a byte dump, or return None when it is text.

    Only the first ``BINARY_SCAN_SIZE`` characters are inspected, so the cost
    does not grow with output size.
    """
    if not text:
        return None
    for magic, description in _BINARY_MAGIC:
        if text.startswith(magic):
            return description
    head = text[:BINARY_SCAN_SIZE]
    if "\x00" in head:
        return "binary data"
    if head.count("�") / len(head) >= BINARY_REPLACEMENT_RATIO:
        return "binary data"
    return None


def _split_binary_prefix(text: str) -> tuple[str, str, int]:
    """Split a binary stream into the leading byte dump and the readable rest.

    One call usually runs several commands into the same stream, so a dump is
    routinely followed by another command's output — `cat x.pdf | head -c 100`
    then `ls`. The boundary is the last byte that could not be decoded: every
    NUL and U+FFFD belongs to the dump, and whatever trails the last one is
    text. Cutting there rather than at a line break is deliberate — `head -c N`
    and an archive's trailer both end mid-line, so a line-wise cut would take
    the following command's first line with them.

    The whole stream is scanned, not just ``BINARY_SCAN_SIZE``: the cut needs
    the LAST marker, and one `str.rfind` pass over a stream this size is free.

    What trails the cut is not always another command's output — it is just as
    often the document's own ASCII structure, and no byte in the stream says
    which. A PDF puts its font tables, xref and trailer after the last
    compressed stream, and one that compresses nothing has no marker past its
    header comment at all: measured across the seed corpus of a real world,
    these tails run from ~900 characters to the whole 9 MB file. Keeping them
    unbounded would hand the model the entire output budget for a plain
    `cat x.pdf`, which is what the notice exists to prevent. So only the last
    ``MAX_TRAILING_TEXT`` characters are kept — enough for the diagnostic
    `echo`/`ls` that motivated this, bounded for everything else — and the
    caller is told how much was dropped.

    Returns ``(text, "", 0)`` when there is nothing to keep: a stream with no
    markers at all is one only the magic check caught, and it has no boundary
    to cut at.
    """
    cut = max(text.rfind("\x00"), text.rfind("�")) + 1
    if not cut:
        return text, "", 0
    remainder = text[cut:]
    if not remainder or _binary_format(remainder) is not None:
        return text, "", 0
    if len(remainder) <= MAX_TRAILING_TEXT:
        return text[:cut], remainder, 0
    kept = remainder[-MAX_TRAILING_TEXT:]
    # Start on a line boundary so the kept text does not open mid-token — but
    # only when that leaves something. A single long line (a one-line `ls -m`,
    # a CSV row) puts its only newline at the end of the window, and trimming
    # past it would discard the output this function exists to preserve.
    line_start = kept.find("\n")
    if line_start != -1 and line_start + 1 < len(kept):
        kept = kept[line_start + 1 :]
    return text[:cut], kept, len(remainder) - len(kept)


def _scrub_stream(text: str, stream: str) -> str:
    """Swap a binary stream for a short notice; return text streams untouched.

    Handing the model an escaped byte stream costs it the whole context window
    and tells it nothing, so the notice says what the stream looked like and
    what to do instead. Readable text trailing the dump is kept after the
    notice, and the count describes only what the notice replaced.
    """
    description = _binary_format(text)
    if description is None:
        return text
    suppressed, kept, dropped = _split_binary_prefix(text)
    notice = (
        f"[{stream} suppressed: {len(suppressed):,} characters of what looks "
        f"like {description}, not readable text. Write binary output to a file "
        f"instead (e.g. `curl -o out.bin <url>`, or `command > out.bin`) and "
        f"read that file with a tool for the format.]"
    )
    if not kept:
        return notice
    if dropped:
        notice += (
            f"\n[{dropped:,} further characters of it were readable but are not "
            f"shown; the last {len(kept):,} follow.]"
        )
    return f"{notice}\n{kept}"


def _replace_binary_streams(result: SandboxResult) -> SandboxResult:
    """Scrub binary output stream by stream, before the streams are combined.

    Per stream because the two usually differ: a command that writes a file to
    stdout puts its real diagnostics on stderr, and those are the part worth
    reading.
    """
    return dataclasses.replace(
        result,
        stdout=_scrub_stream(result.stdout, "stdout"),
        stderr=_scrub_stream(result.stderr, "stderr"),
    )


def _sanitize_output(text: str) -> str:
    """Truncate oversized output, with aggressive limits for HTML blobs.

    Keeps a head and a tail. A long run's opening says what it was doing and its
    closing lines say how it finished, so a head-only cap drops the half that
    usually carries the answer.
    """
    if not text:
        return text
    limit = MAX_HTML_OUTPUT_SIZE if _looks_like_html(text) else MAX_OUTPUT_SIZE
    if len(text) <= limit:
        return text
    tail = limit // TRUNCATION_TAIL_FRACTION
    head = limit - tail
    return (
        text[:head] + f"\n\n[output truncated — {len(text):,} chars total, "
        f"showing first {head:,} and last {tail:,}]\n\n" + text[-tail:]
    )


def _run_in_sandbox(command: str, timeout: int) -> SandboxResult:
    """Run ``command`` via ContainerSandbox.run_shell, as upstream's SandboxResult."""
    from atoll_env.sandbox import ContainerSandbox

    raw = ContainerSandbox().run_shell(command, get_workspace_root(), timeout=timeout)
    if "error" in raw:
        return SandboxResult(stdout="", stderr="", return_code=-1, error=raw["error"])
    return SandboxResult(
        stdout=raw["stdout"],
        stderr=raw["stderr"],
        return_code=raw["exit_code"],
        timed_out=raw["timed_out"],
    )


@register("code_execution_server")
def code_exec(
    request: Annotated[
        CodeExecRequest | None,
        Field(description="Alternate wrapper form. Prefer the top-level `code` parameter."),
    ] = None,
    code: Annotated[str | None, Field(description=CODE_DESCRIPTION)] = None,
) -> CodeExecResponse:
    # The command arrives either flat (``code``, as the description and README
    # document it) or inside the ``request`` wrapper this tool has advertised
    # since #10. Both are accepted; a flat ``code`` wins when both are sent.
    if request is not None and not isinstance(request, CodeExecRequest):
        # Upstream's gateway validated this; natively the raw dict/str arrives here.
        request = CodeExecRequest.model_validate(request)
    if code is None and request is not None:
        code = request.code

    # Reject None code - allow empty string (valid in bash)
    if code is None:
        return CodeExecResponse(
            success=False,
            output="Error: Required parameter 'code' (command to execute)",
        )

    # Safety net: detect raw Python code and provide helpful error
    code_stripped = code.strip()

    def looks_like_python_import(code: str) -> bool:
        """Check if code looks like a Python import vs shell command.

        'import' is also an ImageMagick command for screenshots, e.g.:
        - import screenshot.png
        - import -window root desktop.png

        Python imports look like:
        - import module
        - import module.submodule
        - import module as alias
        """
        if not code.startswith("import "):
            return False
        rest = code[7:].strip()  # After "import "
        # Shell import typically has options (-flag) or file paths
        if rest.startswith("-") or "/" in rest.split()[0] if rest else False:
            return False
        # Shell import targets typically have file extensions
        first_word = rest.split()[0] if rest else ""
        if "." in first_word and first_word.rsplit(".", 1)[-1].lower() in (
            "png",
            "jpg",
            "jpeg",
            "gif",
            "bmp",
            "tiff",
            "webp",
            "pdf",
            "ps",
            "eps",
        ):
            return False
        return True

    python_indicators = (
        looks_like_python_import(code_stripped),
        code_stripped.startswith("from "),
        code_stripped.startswith("def "),
        code_stripped.startswith("class "),
        code_stripped.startswith("async def "),
        code_stripped.startswith("@"),  # decorators
    )
    if any(python_indicators):
        return CodeExecResponse(
            success=False,
            output=(
                "Error: It looks like you passed raw Python code. This tool executes shell "
                "commands, not Python directly. To run Python:\n"
                "• One-liner: python -c 'your_code_here'\n"
                "• Multi-line: Write to file first, then run:\n"
                "  cat > script.py << 'EOF'\n"
                "  your_code\n"
                "  EOF && python script.py"
            ),
        )

    code_exec_command_timeout = os.getenv("CODE_EXEC_COMMAND_TIMEOUT", "300")
    try:
        timeout_value = int(code_exec_command_timeout)
    except ValueError:
        error_msg = f"Invalid timeout value: {code_exec_command_timeout}"
        return CodeExecResponse(
            success=False,
            output=f"Configuration error: {error_msg}",
        )

    result = _run_in_sandbox(code, timeout_value)
    result = _replace_binary_streams(result)

    if result.timed_out:
        output = f"Command execution timed out after {timeout_value} seconds"
        # Include partial stdout/stderr if captured (helps user debug)
        if result.stdout or result.stderr:
            output += "\n\nPartial output before timeout:"
            if result.stdout:
                output += f"\nStdout:\n{result.stdout}"
            if result.stderr:
                output += f"\nStderr:\n{result.stderr}"
        return CodeExecResponse(
            success=False,
            output=_sanitize_output(output),
        )

    if result.error:
        output = f"System error: {result.error}"
        # Include partial stdout/stderr if captured (e.g. from exception during run)
        if result.stdout or result.stderr:
            output += "\n\nOutput before error:"
            if result.stdout:
                output += f"\nStdout:\n{result.stdout}"
            if result.stderr:
                output += f"\nStderr:\n{result.stderr}"
        return CodeExecResponse(
            success=False,
            output=_sanitize_output(output),
        )

    if result.return_code != 0:
        output = result.stdout if result.stdout else ""
        if result.stderr:
            output += f"\nError output:\n{result.stderr}"
        return CodeExecResponse(
            success=False,
            output=_sanitize_output(
                f"{output}\n\nCommand failed with exit code {result.return_code}"
            ),
        )

    output = result.stdout or ""
    if result.stderr:
        stderr_stripped = result.stderr.strip()
        if stderr_stripped:
            if output.strip():
                output = f"{output.rstrip()}\n\nStderr output:\n{stderr_stripped}"
            else:
                output = stderr_stripped

    return CodeExecResponse(
        success=True,
        output=_sanitize_output(output),
    )


# Upstream registers the tool via ``mcp.tool(code_exec)``, so the model-visible
# description is the function docstring. Assigned here (verbatim) so the long
# single-line upstream text survives source formatting untouched.
code_exec.__doc__ = (
    "Execute a shell command in a sandboxed environment. The 'code' parameter is a "
    "shell command string (e.g., 'ls -la', 'pip install pandas'). To run Python, use "
    "'python -c \"your_code\"' or write a script file and execute it. Returns stdout, "
    "stderr, and exit code. Do NOT pass raw Python code directly. /proc and /sys are "
    "unavailable, so process tools such as ps, top and pgrep do not work."
)
