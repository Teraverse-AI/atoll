# grading

Deferred — not implemented yet (see project brief NOTE).

This directory mirrors Archipelago's `grading/` boundary and will hold the verifier
runner. The contract it will consume already exists:

- `initial_snapshot.tar.gz` / `final_snapshot.tar.gz` from each run directory
- `trajectory.jsonl` / `result.json` (agent output)
- each task's `gold_answer.json` and the `[[atoll.verifiers]]` entries in `task.toml`

Planned verifiers: `debit_credit`, `numeric_tolerance`, `file_exists`,
`rubric_llm_judge`. Output: `grades.json` with `final_score` in [0.0, 1.0].
