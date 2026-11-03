# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Path resolution for sheets tools (port of utils/path_utils.py).

Divergence: Archipelago resolves model-visible paths against mcp_actor's
per-actor filesystem root; Atoll resolves them against the per-run workspace
root (``atoll_tools.context``). ``PathTraversalError`` maps to Atoll's
exception of the same name, and every error string the tools emit is unchanged.
"""

import os

from atoll_tools.context import (
    PathTraversalError,
    get_workspace_root,
    resolve_in_workspace,
)

__all__ = [
    "PathTraversalError",
    "get_sheets_root",
    "resolve_new_file_path",
    "resolve_under_root",
]


def get_sheets_root() -> str:
    return str(get_workspace_root())


def resolve_under_root(path: str) -> str:
    return str(resolve_in_workspace(path))


def resolve_new_file_path(directory: str, filename: str) -> str:
    """Resolve a path for a new file to be created.

    Args:
        directory: Directory path (may include leading slash)
        filename: The filename (should not include path separators)

    Returns:
        The fully resolved path for the new file

    Raises:
        PathTraversalError: If the resolved path escapes the sandbox
        ValueError: If filename contains path separators
    """
    if os.sep in filename or (os.altsep and os.altsep in filename):
        raise ValueError(f"Filename cannot contain path separators: {filename}")

    directory = directory.rstrip("/")
    if directory:
        path = f"{directory}/{filename}"
    else:
        path = filename

    return resolve_under_root(path)
