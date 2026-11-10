[中文](./README.md) | English

# Task metadata schema

[`metadata.schema.json`](./metadata.schema.json) is the JSON Schema (draft 2020-12) for delivered task metadata. One file describes both kinds of task:

| Kind | File | Definition | Told apart by |
|---|---|---|---|
| Agent task | `metadata.json` in the task folder | `#/$defs/agent` | has `framework: "atoll"` |
| QA task | a single `<task_id>.json` | `#/$defs/qa` | has `qa_type` |

The top-level schema requires a document to be exactly one of the two (`oneOf`); either definition can also be referenced on its own. An Agent task's `reference/rubrics.json` validates against `#/$defs/rubrics`.

## Fields

| Field | Agent | QA | Meaning |
|---|---|---|---|
| `internal_id` | ✓ | | the author's internal id (a GUID) |
| `task_id` | ✓ | ✓ | the task's id; Teraverse's convention below |
| `framework` | ✓ | | always `"atoll"` |
| `subject` | ✓ | ✓ | subject, e.g. `会计`, `综合` |
| `tags` | ✓ | ✓ | sub-topic tags (one string) |
| `qa_type` | | ✓ | `客观` objective / `主观` subjective |
| `question_type` | | ✓ | `SC` single choice, `MC` multiple choice, `CA` calculation, `NA` none |
| `prompt` | ✓ | ✓ | the task text: the run's user message |
| `reference_answer` | | ✓ | the reference answer (an Agent task's reference outputs are in `reference/outputs/`) |
| `rubrics` | | ✓ | the rubric (an Agent task's is `reference/rubrics.json`) |
| `common_errors` | ✓ | ✓ | typical mistakes |
| `knowledge_points` | ✓ | ✓ | knowledge and standards covered |
| `author` | ✓ | ✓ | publisher |
| `date` | ✓ | ✓ | the day the task was finished, `YYYY-MM-DD` |
| `difficulty` | ✓ | ✓ | `L1` (easiest) – `L3` (hardest), on the best model's mean score |
| `evaluations` | ✓ | ✓ | per model: `{"runs": [...], "mean_score": …}` |

Each run in `evaluations`: Agent `{score, date}`; QA `{response, score, correct, date}`, where `correct` (0/1: does the final answer match the reference answer) is required for objective (`客观`) tasks only. `score` is the weighted rubric score, a number in percent.

Each rubric item: `rubric_number`, `rubric_tag`, `rubric_weight` (> 0 credit, < 0 penalty, 0 veto), `rubric_detail`.

## Task id convention

Tasks published by Teraverse use `AG-0001-ACC-v1` (Agent) / `QA-0001-ACC-v1` (QA): {kind}-{4-digit sequence, one per kind}-{subject acronym}-v{version}. Subject acronyms: `ACC` accounting, `AUD` audit, `CFM` corporate finance & management, `TAX` tax, `MIX` cross-subject, `OTH` other. Atoll itself accepts any id, and the schema does not enforce this format.

## Validating

```bash
uv run python -c "
import json, sys
from jsonschema import Draft202012Validator
schema = json.load(open('schema/metadata.schema.json'))
for e in Draft202012Validator(schema).iter_errors(json.load(open(sys.argv[1]))):
    print(e.json_path, e.message)
" path/to/metadata.json
```

No output means valid. Tests: `uv run pytest schema/tests`.
