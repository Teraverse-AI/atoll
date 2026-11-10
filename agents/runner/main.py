"""CLI entry point: run one task end-to-end and write its run artifacts.

Usage:
    uv run atoll-run --task <task-dir> --model <model>

Two task layouts:

* **bundle** — task.toml + instruction.md + workspace/. Run dir (--output, default
  runs/<task_id>/<timestamp>/) holds workspace/ (the agent's working copy) next to the
  files below.
* **delivery** — prompt.md (the user message, never shown as a file) + filesystem/
  (mounted at /filesystem), optional system_prompt.md and task.toml. Run dir (--output,
  default <task>/results/<model>/run_<next n>/) holds agent_outputs/ (the files the
  agent added or changed) instead of workspace/, which is removed after the run.

Every run dir holds:
    initial_snapshot.tar.gz
    final_snapshot.tar.gz
    trajectory.jsonl      one JSON message per line, then one {"type": "result"} line
    result.json           the full AgentTrajectoryOutput, plus the run's trajectory_id
                          (the x-atoll-trajectory spend tag)

<model> is the model alias's last segment (``team/model-a`` → ``model-a``),
or --model-name — e.g. to file a direct-to-provider model ID under the gateway's name.
"""

import argparse
import asyncio
import json
import os
import shutil
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from atoll_env.snapshot import changed_since, save_snapshot
from atoll_env.workspace import create_workspace
from dotenv import load_dotenv

from runner.loop import LoopTruncatedToolsAgent
from runner.models import AgentRunInput
from runner.providers.native import NativeToolProvider
from runner.utils.decorators import task_id_ctx

SYSTEM_PROMPTS_DIR = Path(__file__).parent / "system_prompts"
# Atoll's default system prompt — edit freely. It started as a copy of the pinned
# reference below.
DEFAULT_SYSTEM_PROMPT_FILE = SYSTEM_PROMPTS_DIR / "atoll_agents.md"
# Reference only (never loaded by default): Mercor's APEX-Agents system prompt,
# verbatim (paper Appendix D.2; upstream react_toolbelt_agent/README.md
# "Recommended System Prompt" at the pinned commit).
APEX_AGENTS_SYSTEM_PROMPT_FILE = SYSTEM_PROMPTS_DIR / "apex_agents.md"


def resolve_system_prompt(task_dir: Path, task: dict, cli_file: Path | None) -> tuple[str, str]:
    """The run's system prompt and where it came from.

    Precedence (per-task wins): task.toml ``[atoll] system_prompt_file`` (relative
    to the task dir) > global override (``--system-prompt-file``, else
    ``$ATOLL_SYSTEM_PROMPT_FILE``) > Atoll's default (``atoll_agents.md``). Leading and
    trailing whitespace is stripped.
    """
    if task.get("system_prompt_file"):
        path, source = task_dir / task["system_prompt_file"], "task"
    elif cli_file is not None:
        path, source = cli_file, "global (--system-prompt-file)"
    elif os.environ.get("ATOLL_SYSTEM_PROMPT_FILE"):
        path, source = Path(os.environ["ATOLL_SYSTEM_PROMPT_FILE"]), "global ($ATOLL_SYSTEM_PROMPT_FILE)"
    else:
        path, source = DEFAULT_SYSTEM_PROMPT_FILE, "default"
    if not path.is_file():
        raise FileNotFoundError(f"system prompt file not found ({source}): {path}")
    return path.read_text(encoding="utf-8").strip(), f"{source}: {path}"


_ON = {"on", "true", "yes", "1"}
_OFF = {"off", "false", "no", "0"}


def resolve_office_tools(task: dict, cli_value: str | None) -> bool:
    """Whether the run mounts Atoll's opt-in office tools (LibreOffice-backed).

    Precedence like the stop conditions: ``--office-tools`` > task.toml ``[atoll]
    office_tools`` > ``$ATOLL_OFFICE_TOOLS`` > off. Off keeps the model-visible tool
    surface identical to Archipelago's.
    """
    for value, source in ((cli_value, "--office-tools"),
                          (task.get("office_tools"), "task.toml [atoll].office_tools"),
                          (os.environ.get("ATOLL_OFFICE_TOOLS") or None, "$ATOLL_OFFICE_TOOLS")):
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in _ON:
            return True
        if text in _OFF:
            return False
        raise ValueError(f"invalid office_tools setting {value!r} from {source} (use on/off)")
    return False


def is_delivery_task(task_dir: Path) -> bool:
    """Delivery layout: prompt.md + filesystem/ at the task root."""
    return (task_dir / "prompt.md").is_file() and (task_dir / "filesystem").is_dir()


def _task_id(task_dir: Path, config: dict) -> str:
    """task.toml [metadata] id > metadata.json task_id > the directory name."""
    if task_id := config.get("metadata", {}).get("id"):
        return task_id
    meta = task_dir / "metadata.json"
    if meta.is_file():
        task_id = json.loads(meta.read_text(encoding="utf-8")).get("task_id")
        if task_id:
            return str(task_id)
    return task_dir.name


def load_task(task_dir: Path) -> dict:
    """Read a task: a bundle (task.toml + instruction.md + workspace/) or a delivery
    task (prompt.md + filesystem/, task.toml optional)."""
    delivery = is_delivery_task(task_dir)
    toml_path = task_dir / "task.toml"
    if not delivery or toml_path.exists():
        config = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    else:
        config = {}
    atoll = config.get("atoll", {})
    instruction_file = atoll.get("instruction_file", "prompt.md" if delivery else "instruction.md")
    system_prompt_file = atoll.get("system_prompt_file")
    if delivery and system_prompt_file is None and (task_dir / "system_prompt.md").is_file():
        system_prompt_file = "system_prompt.md"
    return {
        "id": _task_id(task_dir, config),
        "delivery": delivery,
        "instruction": (task_dir / instruction_file).read_text(encoding="utf-8"),
        "workspace_template": task_dir / atoll.get("workspace_dir", "filesystem" if delivery else "workspace"),
        "agent": config.get("agent", {}),
        # Which tool servers the run mounts (like Archipelago's mcp_config);
        # omitted = all built-in servers.
        "servers": atoll.get("servers"),
        "system_prompt_file": system_prompt_file,
        # Atoll's opt-in office tools (LibreOffice-backed); omitted = $ATOLL_OFFICE_TOOLS, else off.
        "office_tools": atoll.get("office_tools"),
    }


def model_dir_name(model: str) -> str:
    """The model alias without its routing prefixes: ``responses/team/model-a`` → ``model-a``."""
    return model.rstrip("/").rsplit("/", 1)[-1]


def next_run_dir(results_model_dir: Path) -> Path:
    """``run_<n>`` with n one past the highest existing run (run_1 first)."""
    taken = [int(p.name.removeprefix("run_")) for p in results_model_dir.glob("run_*")
             if p.name.removeprefix("run_").isdigit()]
    return results_model_dir / f"run_{max(taken, default=0) + 1}"


def write_agent_outputs(initial_snapshot: Path, workspace: Path, dest: Path) -> list[str]:
    """Copy the files the agent added or changed (vs. the initial snapshot) into ``dest``."""
    changed = changed_since(initial_snapshot, workspace)
    dest.mkdir(parents=True, exist_ok=True)
    for rel in changed:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(workspace / rel, target)
    return changed


def write_trajectory(path: Path, output, trajectory_id: str | None = None) -> None:
    """trajectory.jsonl: one message per line, then a final result summary line."""
    with path.open("w", encoding="utf-8") as fh:
        for i, message in enumerate(output.messages):
            fh.write(json.dumps({"index": i, **message}, ensure_ascii=False) + "\n")
        summary = output.model_dump(mode="json", exclude={"messages"})
        if trajectory_id:
            summary = {"trajectory_id": trajectory_id, **summary}
        fh.write(json.dumps({"type": "result", **summary}, ensure_ascii=False) + "\n")


def build_agent_config(agent_table: dict, args) -> dict:
    """The task.toml [agent] table as agent_config_values, with CLI/env overrides.

    Keys are upstream's exact config names (max_steps, timeout, token_budget,
    tool_call_timeout, llm_response_timeout, ...). Precedence:
    CLI flag > task.toml > env var > Atoll harness default. Atoll's harness
    defaults are max_steps=250, timeout=3600, token budget off (the ported loop
    keeps upstream's own internal defaults for library use).
    """
    config = dict(agent_table)
    for key, cli_value, env_key, default in (
        ("max_steps", args.max_steps, "MAX_STEPS", 250),
        ("timeout", args.timeout, "TIMEOUT", 3600),
        ("token_budget", args.token_budget, "TOKEN_BUDGET", None),
    ):
        if cli_value is not None:
            config[key] = cli_value
        elif key not in config:
            if os.environ.get(env_key):
                config[key] = int(os.environ[env_key])
            elif default is not None:
                config[key] = default
    return config


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="atoll-run", description="Run an Atoll task")
    parser.add_argument("--task", type=Path, required=True, help="task directory: a bundle (task.toml) or a delivery task (prompt.md + filesystem/)")
    parser.add_argument("--model", default=os.environ.get("MODEL_ALIAS", ""),
                        help="LiteLLM model alias (default: $MODEL_ALIAS)")
    parser.add_argument("--model-name", default=None,
                        help="results/<model> folder name for delivery tasks (default: the "
                             "--model alias's last segment)")
    parser.add_argument("--output", type=Path, default=None,
                        help="run output dir (default: bundle runs/<task_id>/<timestamp>; "
                             "delivery <task>/results/<model>/run_<next n>)")
    parser.add_argument("--workspace", type=Path, default=None,
                        help="exact (empty) workspace dir to use (default: a fresh "
                             "ws_* dir under the run dir)")
    parser.add_argument("--system-prompt-file", type=Path, default=None,
                        help="global system prompt override (default: $ATOLL_SYSTEM_PROMPT_FILE, "
                             "else system_prompts/atoll_agents.md); a task's own "
                             "[atoll] system_prompt_file still wins")
    parser.add_argument("--office-tools", choices=["on", "off"], default=None,
                        help="mount Atoll's opt-in LibreOffice tools (default: task.toml "
                             "[atoll] office_tools, else $ATOLL_OFFICE_TOOLS, else off)")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--token-budget", type=int, default=None)
    args = parser.parse_args()

    if not args.model:
        parser.error("--model is required (or set MODEL_ALIAS in .env)")

    task = load_task(args.task)
    office_tools = resolve_office_tools(task, args.office_tools)
    system_prompt, system_prompt_source = resolve_system_prompt(
        args.task, task, args.system_prompt_file
    )
    if args.output:
        run_dir = args.output
    elif task["delivery"]:
        run_dir = next_run_dir(args.task / "results" / (args.model_name or model_dir_name(args.model)))
    else:
        run_dir = Path("runs") / task["id"] / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    workspace = create_workspace(
        task["workspace_template"], base_dir=run_dir, target_dir=args.workspace
    )
    save_snapshot(workspace, run_dir / "initial_snapshot.tar.gz")

    task_id_ctx.set(task["id"])  # spend tags: upstream's task_id + x-atoll-task
    run_input = AgentRunInput(
        trajectory_id=str(uuid.uuid4()),
        initial_messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task["instruction"]},
        ],
        workspace_dir=str(workspace),
        orchestrator_model=args.model,
        orchestrator_extra_args=None,
        agent_config_values=build_agent_config(task["agent"], args),
    )

    from atoll_tools.registry import load_tools

    provider = NativeToolProvider(
        registry=load_tools(task["servers"], office_tools=office_tools), workspace_root=workspace
    )
    agent = LoopTruncatedToolsAgent(run_input, provider)
    output = asyncio.run(agent.run())

    save_snapshot(workspace, run_dir / "final_snapshot.tar.gz")
    write_trajectory(run_dir / "trajectory.jsonl", output, run_input.trajectory_id)
    (run_dir / "result.json").write_text(
        json.dumps({"trajectory_id": run_input.trajectory_id, **output.model_dump(mode="json")},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if task["delivery"]:
        write_agent_outputs(run_dir / "initial_snapshot.tar.gz", workspace,
                            run_dir / "agent_outputs")
        # A launcher that mounts the workspace elsewhere (the sandbox) may keep the
        # directory itself busy; it removes the emptied directory afterwards.
        for child in workspace.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        try:
            workspace.rmdir()
        except OSError:
            pass

    usage = output.usage or {}
    print(f"task:         {task['id']}")
    print(f"system prompt: {system_prompt_source}")
    print(f"office tools: {'on' if office_tools else 'off'}")
    print(f"status:       {output.status}")
    print(f"llm calls:    {len(usage.get('call_log', []))}")
    print(f"total tokens: {usage.get('total_tokens', 0)}")
    print(f"run dir:      {run_dir}")
    if output.error:
        print(f"error:        {output.error}")
    if output.final_answer:
        print(f"\nfinal answer:\n{output.final_answer}")


if __name__ == "__main__":
    main()
