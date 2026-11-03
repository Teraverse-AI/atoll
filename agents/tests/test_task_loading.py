import json
from pathlib import Path

from runner.main import load_task, write_trajectory
from runner.models import AgentStatus, AgentTrajectoryOutput

REPO = Path(__file__).resolve().parent.parent.parent


def test_load_example_task():
    task = load_task(REPO / "examples" / "q001_adjusting_entry")
    assert task["id"] == "q001_adjusting_entry"
    assert "adjusting" in task["instruction"].lower()
    assert task["workspace_template"].name == "workspace"
    assert task["agent"]["max_steps"] == 25
    assert task["agent"]["timeout"] == 1800  # renamed from timeout_sec


def test_write_trajectory_jsonl(tmp_path):
    output = AgentTrajectoryOutput(
        messages=[{"role": "user", "content": "hi"}, {"role": "assistant", "content": "done"}],
        status=AgentStatus.COMPLETED,
        time_elapsed=1.5,
        usage={"total_tokens": 10, "call_log": []},
        final_answer="done",
    )
    path = tmp_path / "trajectory.jsonl"
    write_trajectory(path, output)
    lines = [json.loads(line) for line in path.read_text().splitlines()]
    assert lines[0] == {"index": 0, "role": "user", "content": "hi"}
    assert lines[-1]["type"] == "result"
    assert lines[-1]["status"] == "completed"
    assert lines[-1]["final_answer"] == "done"
    assert lines[-1]["usage"]["total_tokens"] == 10
    assert "messages" not in lines[-1]
