"""Snapshot a workspace directory as a gzipped tar, Harbor-style."""

import io
import tarfile
from pathlib import Path


def snapshot_workspace(workspace: str | Path) -> io.BytesIO:
    """Tar the workspace's current state into an in-memory buffer.

    The archive stores paths relative to the workspace root, so an initial and a
    final snapshot of the same run are directly diffable by graders.
    """
    root = Path(workspace)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path in sorted(root.rglob("*")):
            tar.add(path, arcname=str(path.relative_to(root)))
    buffer.seek(0)
    return buffer


def save_snapshot(workspace: str | Path, dest: str | Path) -> Path:
    """Snapshot the workspace and write the archive to ``dest`` (.tar.gz)."""
    dest_path = Path(dest)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(snapshot_workspace(workspace).getvalue())
    return dest_path
