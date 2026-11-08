"""Fold per-item verdicts into a run's scores.

The weighted fold is the one Atoll's rubrics were written for (credit / penalty /
veto by the sign of ``rubric_weight``), reproduced exactly — including the integer
truncation of weights and the clamp to 0–100.
"""

from __future__ import annotations

import json
from pathlib import Path


def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def fold(rubric: list[dict], verdicts: dict[int, int]) -> dict:
    """Scores of one run.

    Args:
        rubric: the task's rubric items, in order (``rubric_weight`` used).
        verdicts: 1-based rubric position -> verdict (0/1).

    Returns ``score`` (weighted %, Atoll's only metric), ``vetoed`` and the fold's parts.
    """
    pos_total = earned = penalty = 0.0
    veto_nums: list[int] = []
    for i, item in enumerate(rubric, start=1):
        w = int(item.get("rubric_weight") or 0)  # truncation toward zero, as the rubrics assume
        v = _clamp01(verdicts.get(i, 0))
        if w > 0:
            pos_total += w
            earned += v * w
        elif w < 0:
            penalty += abs(w) * v
        elif v >= 0.5:
            veto_nums.append(i)
    net = earned - penalty
    pct = (net / pos_total * 100) if pos_total > 0 else 0.0
    pct = max(0.0, min(100.0, pct))
    if veto_nums:
        pct = 0.0
    return {
        "score": round(pct, 4),
        "vetoed": bool(veto_nums),
        "earned": earned,
        "penalty": penalty,
        "pos_total": pos_total,
        "veto_nums": veto_nums,
    }


def summarize(results_dir: Path) -> dict:
    """Per-model ``mean_score`` over the runs graded under a delivery's ``results/``.

    Reads ``<results_dir>/<model>/run_<n>/score.json``; models without a graded run
    are left out.
    """
    models = {}
    for model_dir in sorted(p for p in results_dir.iterdir() if p.is_dir()):
        runs = []
        for score_file in sorted(model_dir.glob("run_*/score.json"),
                                 key=lambda f: int(f.parent.name.removeprefix("run_") or 0)):
            s = json.loads(score_file.read_text(encoding="utf-8"))
            runs.append({"run": score_file.parent.name, "score": s["score"],
                         "graded_at": s.get("graded_at")})
        if runs:
            models[model_dir.name] = {
                "runs": runs,
                "mean_score": round(sum(r["score"] for r in runs) / len(runs), 4),
            }
    return models
