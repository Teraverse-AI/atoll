# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Private path helpers shared by the filesystem_server tools.

Local equivalents of upstream ``utils/path_utils.py``: ``virtual_path`` mirrors
``virtual_path_from_physical`` / ``display_path`` and ``is_within_workspace``
mirrors ``is_path_within_sandbox``, both against the per-run Atoll workspace
root instead of the mcp_actor sandbox. Divergence: upstream's opt-in physical
path mode (EXPOSE_PHYSICAL_PATHS) and the ENV-494 mount-spelling special case
are not replicated — Atoll always reports '/'-rooted virtual paths, which is
upstream's default mode.
"""

import os
from pathlib import Path

from atoll_tools.context import get_workspace_root


def virtual_path(physical: str | Path) -> str:
    """The '/'-rooted model-visible spelling of a physical path inside the workspace."""
    root = get_workspace_root()
    path = Path(os.path.normpath(physical))
    try:
        relative = path.relative_to(root)
    except ValueError:
        return str(path)
    if str(relative) == ".":
        return "/"
    return "/" + relative.as_posix()


def is_within_workspace(path: str | Path) -> bool:
    """Whether a physical path, after resolving symlinks, stays inside the workspace."""
    root = get_workspace_root()
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents
