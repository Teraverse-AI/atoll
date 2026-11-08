# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Spill an over-budget tool result into the run workspace.

A tool result that exceeds the step's tool-result budget is refused by
default: the model is told to narrow its query and none of the payload reaches
it. That is the right answer for a *narrowable* call, but not for an atomic
fetch — id in, whole object out, no range parameter — because there is no
tighter query to make.

Spilling writes those bytes to the run's workspace instead and hands the model
a path. Upstream uploads a tar.gz to the sandbox's ``/filesystem/`` over HTTP;
in Atoll the per-run workspace IS that filesystem — it is shared with the
code-execution container (mounted at ``/filesystem``) and presented to the
filesystem tools as ``'/'`` — so the upload is replaced with a direct local
write and the returned path is the ``'/'``-rooted virtual path the model can
pass to ``read_text_file`` or open from ``python_code_execution``.

Everything here is best-effort: any failure returns ``None`` and the caller
falls back to the existing refusal, so a spill that cannot land degrades to
the refusal behaviour rather than erroring the run.
"""

import logging
import re
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

# Spill names are built from the tool name, so they are ours rather than the
# model's — but they still get reduced to this alphabet before being used as a
# filename, so nothing can walk out of the workspace it lands in.
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def safe_spill_name(tool_name: str, index: int, extension: str = "txt") -> str:
    """A filesystem-safe, collision-free name for one spilled result.

    The counter alone is not unique. A harness agent re-instantiates its inner
    agent per turn, which resets the counter — so turn 2's first spill would
    silently replace turn 1's while the earlier path is still cited in the
    conversation. The random suffix makes the name unique for the workspace's
    lifetime; the counter and tool name stay only because they make the file
    recognisable in a trajectory.
    """
    stem = _UNSAFE_NAME_CHARS.sub("_", tool_name).strip("._-") or "tool_result"
    ext = _UNSAFE_NAME_CHARS.sub("", extension).strip(".") or "txt"
    return f"spill_{index:03d}_{stem[:60]}_{uuid4().hex[:8]}.{ext}"


def spill_to_workspace(
    *,
    workspace_dir: str,
    tool_name: str,
    index: int,
    payload: str,
    extension: str = "txt",
) -> str | None:
    """Write ``payload`` into the workspace and return its '/'-rooted path.

    ``payload`` must be the ORIGINAL tool result, not the character-truncated
    copy prepared for inline delivery: the notice this enables tells the model
    the file is complete, and a truncated base64 body does not even decode.

    The default ``txt`` extension is load-bearing: ``read_text_file`` only
    accepts files with a supported text extension.

    Returns None on any failure so the caller can fall back to the refusal.
    """
    root = Path(workspace_dir) if workspace_dir else None
    if root is None or not root.is_dir():
        logger.warning(
            "Cannot spill oversized tool result: no usable workspace directory "
            f"at {workspace_dir!r}"
        )
        return None

    name = safe_spill_name(tool_name, index, extension)
    try:
        (root / name).write_text(payload, encoding="utf-8", errors="replace")
    except Exception as exc:
        # Deliberately broad: a spill is an optimisation over the refusal, so
        # nothing it can do should be able to fail the step.
        logger.warning(
            f"Cannot spill oversized tool result to {root}: {exc!r} — "
            "falling back to the refusal"
        )
        return None

    path = f"/{name}"
    logger.info(f"Spilled oversized {tool_name} result ({len(payload)} chars) -> {path}")
    return path
