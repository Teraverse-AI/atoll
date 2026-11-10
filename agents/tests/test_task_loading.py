import json
from pathlib import Path

from runner.main import load_task, write_trajectory
from runner.models import AgentStatus, AgentTrajectoryOutput


def test_load_bundle_task(tmp_path):
    t = tmp_path / "bundle"
    (t / "workspace").mkdir(parents=True)
    (t / "instruction.md").write_text("Reconcile the adjusting entry.")
    (t / "task.toml").write_text(
        '[metadata]\nid = "b1"\n\n[agent]\nmax_steps = 25\ntimeout = 1800\n')
    task = load_task(t)
    assert task["id"] == "b1" and not task["delivery"]
    assert "adjusting" in task["instruction"].lower()
    assert task["workspace_template"].name == "workspace"
    assert task["agent"]["max_steps"] == 25
    assert task["agent"]["timeout"] == 1800  # upstream's key name


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


def _delivery_task(root: Path) -> Path:
    t = root / "AG-0001-ACC-v1"
    (t / "filesystem").mkdir(parents=True)
    (t / "filesystem" / "ledger.csv").write_text("a,b\n")
    (t / "prompt.md").write_text("Fix the ledger.")
    (t / "metadata.json").write_text(json.dumps({"task_id": "AG-0001-ACC-v1"}))
    return t


def test_load_delivery_task(tmp_path):
    from runner.main import load_task

    t = _delivery_task(tmp_path)
    task = load_task(t)
    assert task["delivery"] and task["id"] == "AG-0001-ACC-v1"
    assert task["instruction"] == "Fix the ledger."
    assert task["workspace_template"] == t / "filesystem"
    assert task["system_prompt_file"] is None and task["agent"] == {}
    (t / "system_prompt.md").write_text("per-task")
    (t / "task.toml").write_text('[agent]\nmax_steps = 7\n[metadata]\nid = "from-toml"\n')
    task = load_task(t)
    assert task["system_prompt_file"] == "system_prompt.md"
    assert task["agent"]["max_steps"] == 7 and task["id"] == "from-toml"


def test_model_dir_and_next_run(tmp_path):
    from runner.main import model_dir_name, next_run_dir

    assert model_dir_name("responses/team/model-a") == "model-a"
    assert model_dir_name("model-b") == "model-b"
    assert next_run_dir(tmp_path / "m") == tmp_path / "m" / "run_1"
    for n in (1, 2, 10):
        (tmp_path / "m" / f"run_{n}").mkdir(parents=True)
    (tmp_path / "m" / "run_x").mkdir()
    assert next_run_dir(tmp_path / "m") == tmp_path / "m" / "run_11"


def test_delivery_run_writes_results_layout(tmp_path, monkeypatch):
    import sys

    import runner.main as main_mod

    t = _delivery_task(tmp_path)

    async def fake_run(self):
        ws = Path(self._workspace_dir)
        (ws / "ledger.csv").write_text("a,b\n1,2\n")  # changed
        (ws / "out").mkdir()
        (ws / "out" / "report.md").write_text("done")  # added
        msg = "done"
        return AgentTrajectoryOutput(messages=[{"role": "assistant", "content": msg}],
                                     status=AgentStatus.COMPLETED, time_elapsed=1.0,
                                     usage={"call_log": []}, final_answer=msg)

    monkeypatch.setattr(main_mod.LoopTruncatedToolsAgent, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["atoll-run", "--task", str(t), "--model", "team/model-a"])
    main_mod.main()

    run = t / "results" / "model-a" / "run_1"
    assert sorted(p.name for p in run.iterdir()) == [
        "agent_outputs", "final_snapshot.tar.gz", "initial_snapshot.tar.gz", "result.json", "trajectory.jsonl"]
    assert sorted(str(p.relative_to(run / "agent_outputs")) for p in (run / "agent_outputs").rglob("*")
                  if p.is_file()) == ["ledger.csv", "out/report.md"]
    assert (t / "filesystem" / "ledger.csv").read_text() == "a,b\n"  # the task itself is untouched

    from atoll_grading.inputs import load_run

    result = json.loads((run / "result.json").read_text())
    last = json.loads((run / "trajectory.jsonl").read_text().splitlines()[-1])
    assert result["trajectory_id"] and last["trajectory_id"] == result["trajectory_id"]

    graded = load_run(run, tmp_path / "w")
    assert sorted(graded.files) == ["ledger.csv", "out/report.md"]
    assert graded.trajectory_id == result["trajectory_id"]

    # --model-name files a provider's full model ID under a shorter name.
    monkeypatch.setattr(sys, "argv", ["atoll-run", "--task", str(t), "--model",
                                      "provider-x/model-b-20260101", "--model-name", "model-b"])
    main_mod.main()
    assert (t / "results" / "model-b" / "run_1" / "result.json").is_file()
