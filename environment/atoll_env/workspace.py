"""Create and clean per-run workspace directories.

Mirrors the role of Archipelago's environment/runner data-populate stage, minus
S3 and Docker: a task's ``workspace/`` directory is copied into a fresh run
directory that the agent's tools treat as the filesystem root.
"""

import shutil
import tempfile
from pathlib import Path


def create_workspace(
    source_dir: str | Path,
    base_dir: str | Path | None = None,
    target_dir: str | Path | None = None,
) -> Path:
    """Copy a task's workspace template into a fresh, writable run directory.

    Args:
        source_dir: The task's ``workspace/`` template (read-only input files).
        base_dir: Where to create the run workspace; a temp directory by default.
        target_dir: Use exactly this directory instead (created if missing; must be
            empty) — for containerized evals, where the launcher mounts it into the
            companion sandbox before the runner starts.

    Returns:
        Path of the new workspace root.
    """
    source = Path(source_dir)
    if not source.is_dir():
        raise FileNotFoundError(f"workspace template not found: {source}")
    if target_dir is not None:
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            raise FileExistsError(f"workspace directory is not empty: {target}")
    elif base_dir is not None:
        root = Path(base_dir)
        root.mkdir(parents=True, exist_ok=True)
        target = Path(tempfile.mkdtemp(prefix="ws_", dir=root))
    else:
        target = Path(tempfile.mkdtemp(prefix="atoll_ws_"))
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def clean_workspace(workspace: str | Path) -> None:
    """Delete a run workspace created by :func:`create_workspace`."""
    shutil.rmtree(workspace, ignore_errors=True)
