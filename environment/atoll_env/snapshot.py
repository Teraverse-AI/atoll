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


_SKIP_PREFIXES = (".", "~$")  # dotfiles, Office lock files — as the grader skips them


def changed_since(initial_snapshot: str | Path, workspace: str | Path) -> list[str]:
    """Workspace files added or modified since ``initial_snapshot`` (relative paths).

    Dotfiles and Office lock files are left out.
    """
    root = Path(workspace)
    with tarfile.open(initial_snapshot, "r:gz") as tar:
        before = {m.name: tar.extractfile(m).read() for m in tar.getmembers() if m.isfile()}
    changed = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if not path.is_file() or any(p.startswith(_SKIP_PREFIXES) for p in rel.parts):
            continue
        if before.get(str(rel)) != path.read_bytes():
            changed.append(str(rel))
    return changed
