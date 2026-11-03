"""Workspace manager CLI: prepare a run workspace from a task, or snapshot one.

Plain Python — Archipelago's environment is a FastAPI service with an MCP gateway;
Atoll's is a library plus this thin CLI.
"""

import argparse
from pathlib import Path

from atoll_env.snapshot import save_snapshot
from atoll_env.workspace import create_workspace


def main() -> None:
    parser = argparse.ArgumentParser(prog="atoll-workspace", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="copy a task workspace template into a run dir")
    prepare.add_argument("source", type=Path, help="task workspace/ directory")
    prepare.add_argument("--base-dir", type=Path, default=None, help="parent dir for the run workspace")

    snap = sub.add_parser("snapshot", help="tar.gz a workspace's current state")
    snap.add_argument("workspace", type=Path)
    snap.add_argument("dest", type=Path, help="output .tar.gz path")

    args = parser.parse_args()
    if args.command == "prepare":
        print(create_workspace(args.source, args.base_dir))
    elif args.command == "snapshot":
        print(save_snapshot(args.workspace, args.dest))


if __name__ == "__main__":
    main()
