"""CLI entry point: run one task end-to-end and write its run artifacts.

Usage:
    uv run atoll-run --task examples/q001_adjusting_entry --model gpt-4o-mini

Produces under --output (default runs/<task_id>/<timestamp>/):
    workspace/            the agent's working copy of the task workspace
    initial_snapshot.tar.gz
    final_snapshot.tar.gz
    trajectory.jsonl      one JSON message per line, then one {"type": "result"} line
    result.json           the full AgentTrajectoryOutput
"""

import argparse
import asyncio
import json
import os
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path

from atoll_env.snapshot import save_snapshot
from atoll_env.workspace import create_workspace
from dotenv import load_dotenv

from runner.loop import LoopTruncatedToolsAgent
from runner.models import AgentRunInput
from runner.providers.native import NativeToolProvider

DEFAULT_SYSTEM_PROMPT = (
    "You are an accounting assistant working inside a task workspace. "
    "Use the available tools to inspect and edit workspace files. "
    "When you are done, reply without any tool call: state your final answer and "
    "list the files you changed."
)


def load_task(task_dir: Path) -> dict:
    """Read a Harbor-style task bundle: task.toml + instruction.md (+ workspace/)."""
    config = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    atoll = config.get("atoll", {})
    instruction_file = atoll.get("instruction_file", "instruction.md")
    return {
        "id": config.get("metadata", {}).get("id", task_dir.name),
        "instruction": (task_dir / instruction_file).read_text(encoding="utf-8"),
        "workspace_template": task_dir / atoll.get("workspace_dir", "workspace"),
        "agent": config.get("agent", {}),
        # Which tool servers the run mounts (like Archipelago's mcp_config);
        # omitted = all built-in servers.
        "servers": atoll.get("servers"),
    }


def write_trajectory(path: Path, output) -> None:
    """trajectory.jsonl: one message per line, then a final result summary line."""
    with path.open("w", encoding="utf-8") as fh:
        for i, message in enumerate(output.messages):
            fh.write(json.dumps({"index": i, **message}, ensure_ascii=False) + "\n")
        summary = output.model_dump(mode="json", exclude={"messages"})
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
    parser.add_argument("--task", type=Path, required=True, help="task directory (with task.toml)")
    parser.add_argument("--model", default=os.environ.get("MODEL_ALIAS", ""),
                        help="LiteLLM model alias (default: $MODEL_ALIAS)")
    parser.add_argument("--output", type=Path, default=None,
                        help="run output dir (default: runs/<task_id>/<timestamp>)")
    parser.add_argument("--workspace", type=Path, default=None,
                        help="exact (empty) workspace dir to use (default: a fresh "
                             "ws_* dir under the run dir)")
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--token-budget", type=int, default=None)
    args = parser.parse_args()

    if not args.model:
        parser.error("--model is required (or set MODEL_ALIAS in .env)")

    task = load_task(args.task)
    run_dir = args.output or (
        Path("runs") / task["id"] / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    )
    run_dir.mkdir(parents=True, exist_ok=True)

    workspace = create_workspace(
        task["workspace_template"], base_dir=run_dir, target_dir=args.workspace
    )
    save_snapshot(workspace, run_dir / "initial_snapshot.tar.gz")

    run_input = AgentRunInput(
        trajectory_id=str(uuid.uuid4()),
        initial_messages=[
            {"role": "system", "content": DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": task["instruction"]},
        ],
        workspace_dir=str(workspace),
        orchestrator_model=args.model,
        orchestrator_extra_args=None,
        agent_config_values=build_agent_config(task["agent"], args),
    )

    from atoll_tools.registry import load_tools

    provider = NativeToolProvider(
        registry=load_tools(task["servers"]), workspace_root=workspace
    )
    agent = LoopTruncatedToolsAgent(run_input, provider)
    output = asyncio.run(agent.run())

    save_snapshot(workspace, run_dir / "final_snapshot.tar.gz")
    write_trajectory(run_dir / "trajectory.jsonl", output)
    (run_dir / "result.json").write_text(
        output.model_dump_json(indent=2), encoding="utf-8"
    )

    usage = output.usage or {}
    print(f"task:         {task['id']}")
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
