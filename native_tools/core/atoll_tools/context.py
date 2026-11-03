"""Per-run workspace root shared by all filesystem-touching tools.

The tool registry maps names to bare callables (``registry[name](**arguments)``),
so the workspace root cannot travel as an argument — the model must never be able
to pick it. It is ambient state set once per run by the tool provider.
"""

from contextvars import ContextVar
from pathlib import Path

_workspace_root: ContextVar[Path | None] = ContextVar("atoll_workspace_root", default=None)


def set_workspace_root(path: str | Path) -> None:
    """Set the workspace root all tools resolve paths against for this run."""
    _workspace_root.set(Path(path).resolve())


def get_workspace_root() -> Path:
    """The active workspace root. Raises if no run has set one."""
    root = _workspace_root.get()
    if root is None:
        raise RuntimeError("No workspace root set; call set_workspace_root() first")
    return root


class PathTraversalError(Exception):
    """A path resolved outside the workspace root."""


def resolve_in_workspace(path: str) -> Path:
    """Resolve ``path`` (absolute-style '/x' or relative 'x') inside the workspace.

    Mirrors Archipelago's resolve_under_root: the workspace is presented to the
    model as the filesystem root, and any escape (.., symlinks) is rejected.
    """
    root = get_workspace_root()
    candidate = (root / path.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        raise PathTraversalError(path)
    return candidate
