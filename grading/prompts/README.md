# grading prompts

Together with the runner's system prompt
([`agents/runner/system_prompts/atoll_agents.md`](../../agents/runner/system_prompts/atoll_agents.md),
a draft) these are the three prompts that shape a benchmark result.

| File | Role |
|---|---|
| `judge_system.md` | system prompt of the per-item judge: rendering conventions (the vendored extractor's values grid + Number Formats + Formulas blocks, LibreOffice recalculation, large-sheet summaries, what counts as a formula error), binary sign rules, penalty polarity, how to use the reference outputs, output JSON, Chinese rationales |
| `judge_item.md` | user message for one rubric item; placeholders in order `{{task_prompt}}`, `{{final_answer}}`, `{{candidate_files}}`, `{{reference_files}}`, `{{related_rows}}`, `{{rubric_item}}` — the item comes last so everything before it is a per-run shared prefix (cacheable) |

Output: `{"verdict", "rationale"}`; for a penalty item (`rubric_weight < 0`) the judge
first fills `deduction_for` (the error being penalised) and `reference_has_error` (must be
`false`) — see [`../README.en.md`](../README.en.md#how-a-run-is-graded).

Unlike Archipelago's judge, this one sees the reference (golden) outputs — see
[`../README.en.md`](../README.en.md#how-this-differs-from-mercors-grading).
