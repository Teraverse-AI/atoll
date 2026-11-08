"""atoll-grade: grade one run of one task, item by item.

    atoll-grade --task <task_dir> --run <run_dir> --judge-model <model> [--output score.json]

Two phases, which containerized grading runs separately (``scripts/run_grade.sh``):

1. **render** — parse the agent's and the reference files (LibreOffice recalculation
   included) into ``rendered.json``. Touches untrusted, agent-written files and needs
   no network: ``--render-only <dir>``.
2. **judge** — one LLM call per rubric item, then the score fold:
   ``--from-rendered <dir>``. Needs only the judge endpoint.

Without either flag both phases run in one process.

    atoll-grade --summarize <task_dir>

writes ``<task_dir>/results/summary.json``: per model, its graded runs and ``mean_score``
(the mean of ``score`` over the runs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

RENDERED = "rendered.json"


def render(task_dir: Path, run_dir: Path, task_name: str | None = None,
           run_label: str | None = None) -> dict:
    """Phase 1: everything the judge needs, as plain text."""
    from atoll_grading.inputs import load_run, load_task
    from atoll_grading.judge import item_payload
    from atoll_grading.render import render_files

    task = load_task(task_dir, task_name)
    with tempfile.TemporaryDirectory(prefix="atoll-grade-") as work:
        run = load_run(run_dir, work, run_label)
        candidate = asyncio.run(render_files(run.files))
        reference = asyncio.run(render_files(task.reference_files))
    related = {}
    for i, item in enumerate(task.rubric, start=1):
        detail = item.get("rubric_detail", "")
        parts = [candidate.related(detail)]
        ref = reference.related(detail)
        if ref:
            parts.append("（参考答案）" + ref)
        related[str(i)] = "\n".join(p for p in parts if p)
    return {
        "task_id": task.id,
        "run": run.label,
        "trajectory_id": run.trajectory_id,
        "task_prompt": task.prompt,
        "final_answer": run.final_answer,
        "rubric": task.rubric,
        "items": [item_payload(item) for item in task.rubric],
        "candidate_files": candidate.text,
        "reference_files": reference.text,
        "related_rows": related,
        "candidate_file_names": sorted(run.files),
        "reference_file_names": sorted(task.reference_files),
    }


def judge(rendered: dict, model: str, *, concurrency: int, timeout: int, extra_args: dict) -> dict:
    """Phase 2: judge every item and fold the verdicts into score.json's content."""
    from atoll_grading.judge import Prompts, build_user_message, judge_run
    from atoll_grading.score import fold

    prompts = Prompts.load()
    pairs = []
    for i, item in enumerate(rendered["rubric"], start=1):
        pairs.append((item, build_user_message(
            prompts.item,
            task_prompt=rendered["task_prompt"],
            final_answer=rendered["final_answer"],
            candidate_files=rendered["candidate_files"],
            reference_files=rendered["reference_files"],
            related_rows=rendered["related_rows"].get(str(i), ""),
            rubric_item=rendered["items"][i - 1],
        )))
    # Grading spend is tagged with the graded eval run's own trajectory id when it has one.
    graded = rendered.get("trajectory_id") or rendered["run"]
    headers = {"x-atoll-task": str(rendered["task_id"]), "x-atoll-trajectory": f"grade/{graded}"}
    args = {**extra_args, "extra_headers": {**headers, **extra_args.get("extra_headers", {})}}
    results = asyncio.run(judge_run(model, prompts, pairs, concurrency=concurrency, timeout=timeout,
                                    extra_args=args))
    verdicts = {i: r.verdict for i, r in enumerate(results, start=1)}
    scores = fold(rendered["rubric"], verdicts)
    return {
        "task": rendered["task_id"],
        "run": rendered["run"],
        "graded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge": model,
        "score": scores["score"],
        "vetoed": scores["vetoed"],
        "items": [
            {"rubric_number": r.rubric_number, "rubric_weight": r.weight, "verdict": r.verdict,
             "rationale": r.rationale, **r.extra}
            for r in results
        ],
        "usage": {
            "calls": len(results),
            "prompt_tokens": sum(r.usage.get("prompt_tokens", 0) for r in results),
            "completion_tokens": sum(r.usage.get("completion_tokens", 0) for r in results),
        },
    }


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="atoll-grade", description="Grade one Atoll run")
    parser.add_argument("--task", type=Path, help="task dir (Atoll bundle or delivery task)")
    parser.add_argument("--run", type=Path, help="run dir (atoll-run output or delivery run)")
    parser.add_argument("--judge-model", default=os.environ.get("ATOLL_JUDGE_MODEL", ""),
                        help="judge model alias (default: $ATOLL_JUDGE_MODEL)")
    parser.add_argument("--output", type=Path, default=None,
                        help="score.json path (default: <run>/score.json, or <rendered dir>/score.json)")
    parser.add_argument("--task-name", default=None,
                        help="task id when task.toml has none (default: the task dir name)")
    parser.add_argument("--run-label", default=None, help="run label in score.json (default: the run dir name)")
    parser.add_argument("--concurrency", type=int, default=int(os.environ.get("ATOLL_JUDGE_CONCURRENCY", 4)))
    parser.add_argument("--timeout", type=int, default=600, help="per-call LLM timeout (s)")
    parser.add_argument("--judge-extra-args", default=os.environ.get("ATOLL_JUDGE_EXTRA_ARGS", "{}"),
                        help='JSON of extra completion args, e.g. \'{"reasoning_effort": "high"}\'')
    phase = parser.add_mutually_exclusive_group()
    phase.add_argument("--render-only", type=Path, metavar="DIR", help=f"phase 1 only: write DIR/{RENDERED}")
    phase.add_argument("--from-rendered", type=Path, metavar="DIR", help=f"phase 2 only: read DIR/{RENDERED}")
    phase.add_argument("--summarize", type=Path, metavar="TASK_DIR",
                       help="per-model mean_score over TASK_DIR/results/<model>/run_*/score.json")
    args = parser.parse_args(argv)

    if args.summarize is not None:
        from atoll_grading.score import summarize

        results = args.summarize / "results"
        if not results.is_dir():
            parser.error(f"no results/ under {args.summarize}")
        summary = summarize(results)
        out = args.output or (results / "summary.json")
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        for model, m in summary.items():
            print(f"{model}: mean_score {m['mean_score']:.1f} over {len(m['runs'])} run(s)")
        print(f"written: {out}")
        return 0

    if args.from_rendered is None and (args.task is None or args.run is None):
        parser.error("--task and --run are required (unless --from-rendered)")
    if args.render_only is None and not args.judge_model:
        parser.error("--judge-model is required (or set ATOLL_JUDGE_MODEL)")

    from atoll_tools.office import OfficeError

    from atoll_grading.judge import JudgeError

    try:
        if args.from_rendered is not None:
            rendered = json.loads((args.from_rendered / RENDERED).read_text(encoding="utf-8"))
        else:
            rendered = render(args.task, args.run, args.task_name, args.run_label)
        if args.render_only is not None:
            args.render_only.mkdir(parents=True, exist_ok=True)
            (args.render_only / RENDERED).write_text(json.dumps(rendered, ensure_ascii=False), encoding="utf-8")
            print(f"rendered: {args.render_only / RENDERED} ({len(rendered['rubric'])} items)")
            return 0
        score = judge(rendered, args.judge_model, concurrency=args.concurrency, timeout=args.timeout,
                      extra_args=json.loads(args.judge_extra_args))
    except OfficeError as exc:
        print(f"atoll-grade: recalculation failed, run not graded: {exc}", file=sys.stderr)
        return 3
    except JudgeError as exc:
        print(f"atoll-grade: judging failed, run not graded: {exc}", file=sys.stderr)
        return 4

    out = args.output or ((args.from_rendered or args.run) / "score.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(score, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"task:  {score['task']}  run: {score['run']}  judge: {score['judge']}")
    print(f"score: {score['score']:.1f}  vetoed: {score['vetoed']}")
    print(f"tokens: {score['usage']['prompt_tokens']} in / {score['usage']['completion_tokens']} out")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
