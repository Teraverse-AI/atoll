"""Load what the grader consumes: a task (prompt, rubric, reference files) and a run.

Two layouts each:

- **Atoll task bundle** — ``task.toml`` + instruction file; ``[atoll] rubrics``
  (default ``rubrics.json``) and ``[atoll] reference_dir`` (default ``reference/``,
  every file in it except the rubric file is a reference output).
  **Delivery task** — ``prompt.md`` (or, in the earlier tree, ``workspace/prompt.md``),
  ``reference/rubrics.json``, ``reference/outputs/``, and optionally a ``task.toml`` for
  its id and run settings (the delivery files decide the layout, not ``task.toml``); id
  from ``task.toml`` ``[metadata] id``, else ``metadata.json`` ``task_id``, else the
  directory name — the runner's order.
- **Atoll run dir** (written by ``atoll-run``, bundle or delivery task) — ``result.json``
  (final answer) and the files the agent added or changed: the diff between
  ``initial_snapshot.tar.gz`` and ``final_snapshot.tar.gz``. **Delivery run dir** — ``agent_outputs/`` and
  ``cli.json`` (the last ``result`` event carries the final answer).
"""

from __future__ import annotations

import hashlib
import json
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

_SKIP_PREFIXES = (".", "~$")  # dotfiles, Office lock files


@dataclass
class Task:
    id: str
    prompt: str
    rubric: list[dict]
    reference_files: dict[str, Path]


@dataclass
class Run:
    label: str
    final_answer: str
    files: dict[str, Path]  # display path -> file on disk
    trajectory_id: str | None = None  # atoll-run's spend-tag id (result.json)


def _visible(path: Path, root: Path) -> bool:
    return path.is_file() and not any(p.startswith(_SKIP_PREFIXES) for p in path.relative_to(root).parts)


def _files_under(root: Path, exclude: set[Path] = frozenset()) -> dict[str, Path]:
    if not root.is_dir():
        return {}
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*"))
            if _visible(p, root) and p.resolve() not in exclude}


def _load_rubric(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rubric = data["rubrics"] if isinstance(data, dict) else data
    if not isinstance(rubric, list) or not rubric:
        raise ValueError(f"{path}: expected a non-empty list of rubric items")
    return rubric


def load_task(task_dir: str | Path, name: str | None = None) -> Task:
    """``name`` replaces the directory name as the id fallback (e.g. when mounted as /task)."""
    task_dir = Path(task_dir)
    fallback = name or task_dir.name
    toml_path = task_dir / "task.toml"
    config = tomllib.loads(toml_path.read_text(encoding="utf-8")) if toml_path.exists() else {}
    # A delivery task may carry a task.toml too (its id and run settings), so the layout is
    # decided by the delivery files first, as the runner does.
    prompt = task_dir / "prompt.md"
    if not prompt.exists():
        prompt = task_dir / "workspace" / "prompt.md"
    rubric_path = task_dir / "reference" / "rubrics.json"
    if prompt.exists() and rubric_path.exists():
        meta = task_dir / "metadata.json"
        task_id = config.get("metadata", {}).get("id")
        if not task_id and meta.is_file():
            task_id = json.loads(meta.read_text(encoding="utf-8")).get("task_id")
        return Task(
            id=str(task_id) if task_id else fallback,
            prompt=prompt.read_text(encoding="utf-8"),
            rubric=_load_rubric(rubric_path),
            reference_files=_files_under(task_dir / "reference" / "outputs"),
        )
    if toml_path.exists():
        atoll = config.get("atoll", {})
        rubric_path = task_dir / atoll.get("rubrics", "rubrics.json")
        if not rubric_path.exists():
            raise FileNotFoundError(f"rubric not found: {rubric_path} (set [atoll] rubrics)")
        ref_dir = task_dir / atoll.get("reference_dir", "reference")
        return Task(
            id=config.get("metadata", {}).get("id", fallback),
            prompt=(task_dir / atoll.get("instruction_file", "instruction.md")).read_text(encoding="utf-8"),
            rubric=_load_rubric(rubric_path),
            reference_files=_files_under(ref_dir, exclude={rubric_path.resolve()}),
        )
    raise FileNotFoundError(f"{task_dir}: neither task.toml nor prompt.md + reference/rubrics.json")


def _snapshot_files(path: Path) -> dict[str, tuple[str, tarfile.TarInfo]]:
    out = {}
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            fh = tar.extractfile(member)
            out[member.name] = (hashlib.sha256(fh.read()).hexdigest(), member)
    return out


def changed_files(initial: Path, final: Path, dest: Path) -> dict[str, Path]:
    """Files added or modified between two workspace snapshots, extracted under ``dest``."""
    before = _snapshot_files(initial)
    after = _snapshot_files(final)
    wanted = [name for name, (digest, _) in after.items()
              if before.get(name, (None,))[0] != digest
              and not any(p.startswith(_SKIP_PREFIXES) for p in Path(name).parts)]
    files = {}
    with tarfile.open(final, "r:gz") as tar:
        for name in sorted(wanted):
            target = (dest / name).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError(f"snapshot member escapes the workspace: {name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tar.extractfile(name).read())
            files[name] = target
    return files


def load_run(run_dir: str | Path, work_dir: str | Path, label: str | None = None) -> Run:
    """The run's final answer and changed files (Atoll run dir or delivery run dir)."""
    run_dir, work_dir = Path(run_dir), Path(work_dir)
    label = label or run_dir.name
    if (run_dir / "result.json").exists():
        result = json.loads((run_dir / "result.json").read_text(encoding="utf-8"))
        files = changed_files(run_dir / "initial_snapshot.tar.gz", run_dir / "final_snapshot.tar.gz",
                              work_dir / "changed")
        return Run(label, (result.get("final_answer") or "").strip(), files, result.get("trajectory_id"))
    if (run_dir / "agent_outputs").is_dir():
        answer = ""
        cli = run_dir / "cli.json"
        if cli.exists():
            events = json.loads(cli.read_text(encoding="utf-8")).get("events", [])
            results = [e for e in events if e.get("type") == "result"]
            if results:
                answer = (results[-1].get("result") or "").strip()
        return Run(label, answer, _files_under(run_dir / "agent_outputs"))
    raise FileNotFoundError(f"{run_dir}: neither result.json (Atoll run) nor agent_outputs/ (delivery run)")
