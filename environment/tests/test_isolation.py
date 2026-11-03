"""Isolation of containerized evals: N evals in parallel via scripts/run_eval.sh.

Opt-in (slow, needs Docker + both images):
    scripts/build_sandbox_image.sh && scripts/build_runner_image.sh
    ATOLL_ISOLATION_TESTS=1 uv run pytest environment/tests/test_isolation.py -v

Each eval's runner executes atoll_env.isolation_probe instead of the agent loop
(no LLM needed) and reports what its runner and its companion sandbox can reach.
"""

import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
PARALLEL = int(os.environ.get("ATOLL_ISOLATION_PARALLEL", "6"))

pytestmark = pytest.mark.skipif(
    os.environ.get("ATOLL_ISOLATION_TESTS") != "1",
    reason="opt-in: set ATOLL_ISOLATION_TESTS=1 (needs Docker + atoll-runner/atoll-sandbox images)",
)


def _make_task(root: Path) -> Path:
    task = root / "task"
    (task / "workspace").mkdir(parents=True)
    (task / "task.toml").write_text('[metadata]\nid = "isolation_probe"\n')
    (task / "instruction.md").write_text("probe")
    (task / "workspace" / "input.txt").write_text("hello")
    (task / "gold_answer.json").write_text('{"answer": "SECRET-GOLD"}')
    return task


def _run_one(task: Path, root: Path, n: int) -> tuple[int, str, Path]:
    out = root / f"eval{n}"
    env_file = root / f"eval{n}.env"
    env_file.write_text(f"ATOLL_PROBE_SECRET=probe-secret-{n}-do-not-leak\n")
    proc = subprocess.run(
        [str(REPO / "scripts" / "run_eval.sh"), "--task", str(task), "--out", str(out),
         "--env-file", str(env_file), "--", "-m", "atoll_env.isolation_probe"],
        env={**os.environ, "ATOLL_RUNNER_ENTRYPOINT": "python"},
        capture_output=True, text=True, timeout=600,
    )
    return proc.returncode, proc.stdout + proc.stderr, out


def test_parallel_evals_are_isolated(tmp_path):
    task = _make_task(tmp_path)
    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(lambda n: _run_one(task, tmp_path, n), range(PARALLEL)))

    eval_ids = []
    for code, log, out in results:
        report_path = out / "probe.json"
        assert report_path.exists(), f"no probe report in {out}:\n{log}"
        report = json.loads(report_path.read_text())
        eval_ids.append(report["eval_id"])
        failed = {k: report["checks"][k] for k in report["failed"]}
        assert not failed, f"{report['eval_id']} failed: {json.dumps(failed, indent=2)}"
        assert code == 0, log
        # Host side: the workspace holds the task files plus only this eval's marker.
        ws = sorted(p.name for p in (out / "workspace").iterdir())
        assert ws == ["input.txt", f"marker_{report['eval_id']}.txt"], ws
        assert "SECRET-GOLD" not in "".join(
            p.read_text(errors="ignore") for p in (out / "workspace").rglob("*") if p.is_file()
        )

    assert len(set(eval_ids)) == PARALLEL

    # Nothing left behind: containers and channel volumes are removed on exit.
    leftovers = subprocess.run(
        ["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True
    ).stdout + subprocess.run(
        ["docker", "volume", "ls", "--format", "{{.Name}}"], capture_output=True, text=True
    ).stdout
    assert not [i for i in eval_ids if i in leftovers], leftovers
