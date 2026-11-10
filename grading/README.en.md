# grading

[简体中文](./README.md) | **English**

Atoll's grader: one LLM judge call per rubric item over text renderings of the agent's
files and the task's reference (golden) files, then a weighted score fold. It mirrors
Archipelago's `grading/` boundary — it consumes a finished run and writes a score; it
never touches the agent loop.

## Usage

```bash
# one process (host or any container with LibreOffice)
atoll-grade --task <task-dir> --run <run-dir> --judge-model <model> [--output score.json]

# containers, the reference launcher (render with no network; judge via an egress proxy)
scripts/run_grade.sh --task <task-dir> --run <run-dir> --out <grade-dir> \
    --judge-model <model> --env-file .env
```

- **Judge model**: your choice — any LiteLLM model works: with
  `LITELLM_BASE_URL` / `LITELLM_API_KEY` set, calls go to that OpenAI-compatible endpoint
  (pass the alias as `litellm_proxy/<alias>`); unset, litellm routes direct to the
  provider (provider-prefixed model + provider key). Default: `$ATOLL_JUDGE_MODEL`.
- `--concurrency` (default 4, `$ATOLL_JUDGE_CONCURRENCY`), `--timeout` (per call, 600 s),
  `--judge-extra-args` (JSON, e.g. `'{"reasoning_effort": "high"}'`, `$ATOLL_JUDGE_EXTRA_ARGS`).
- Phases: `--render-only <dir>` writes `<dir>/rendered.json`; `--from-rendered <dir>`
  judges it. Without either, both run in one process.
- Exit codes: `0` graded; `3` a workbook could not be recalculated; `4` an item could not
  be judged. A run is only ever scored complete — on 3 or 4 no `score.json` is written.

Calls go through Atoll's vendored LLM layer (`runner.llm.generate_response`): the same
routing, provider request surgery and retry policy as the agent loop. Each call carries
`x-atoll-task` / `x-atoll-trajectory` headers (`grade/<run>`) for spend attribution.

## Inputs

| | Atoll layout | Delivery layout |
|---|---|---|
| task | `task.toml` + instruction file; `[atoll] rubrics` (default `rubrics.json`) and `[atoll] reference_dir` (default `reference/`; every file in it except the rubric is a reference output) | `prompt.md` (or `workspace/prompt.md`), `reference/rubrics.json`, `reference/outputs/`, optionally a `task.toml` (id and run settings); id from `task.toml` `[metadata] id`, else `metadata.json` `task_id` |
| run | `atoll-run` output: `result.json` (final answer) and the files the agent **added or changed** — the diff between `initial_snapshot.tar.gz` and `final_snapshot.tar.gz`, as Archipelago grades | an `atoll-run` output as on the left; or `agent_outputs/` and `cli.json` (the last `result` event is the final answer) |

Rubric items carry `rubric_number`, `rubric_tag`, `rubric_weight`, `rubric_detail`.
Dotfiles and Office lock files (`~$…`) are ignored.

## How a run is graded

- **One judge call per rubric item**, no tools — like Archipelago's `output_llm` verifier.
  The judge sees the task, the final answer, the rendered agent files and reference
  files, the rows of large sheets the item names, and the item last (so everything
  before it is one shared prefix per run that the provider can cache).
- **Binary verdicts**, with the sign of an item's weight deciding its rule: weight > 0
  credit (all requirements met → 1), weight < 0 penalty (any described error present →
  1), weight = 0 veto (condition holds → the whole run scores 0).
- **Penalty polarity is made explicit.** Rubric authors word penalty conditions in
  several ways (`fire 当且仅当 X`, `pass 当且仅当 X`, or a condition naming the *correct*
  answer followed by `则得-N分`). Rubrics are used as written; instead, for every penalty
  item the judge must first state what the deduction is for (`deduction_for`) and
  whether the reference outputs exhibit that error (`reference_has_error`, which must be
  `false`) before giving its verdict — an inverted reading shows up as a reference
  answer being penalised. A penalty answer missing either field is re-asked.
- Unparseable output is re-asked up to 3 times; LLM failures are retried by the vendored
  layer. Prompts: [`prompts/`](./prompts/).

## Rendering

Grading judges what a user opening the files would see.

**Archipelago's extractor, vendored.** Files are rendered by upstream's own grader
extraction layer (`runner/utils/file_extraction`, byte-identical at the pinned commit —
see [`agents/runner/utils/PATCHES.md`](../agents/runner/utils/PATCHES.md)): per visible
sheet a grid of values, then `=== Number Formats ===` (`A1: 1.50 [fmt: 0.00]`) and
`=== Formulas ===` (`C9: =C7-C8`, as authored) blocks; documents, presentations and PDFs
as their text and tables. Each file is headed `=== File: <name> ===`, with
`(recalculated by LibreOffice)` when the workbook was recalculated.

**Atoll's patches** (runtime, in `atoll_grading/render.py`; no vendored file is edited):

1. **Forced full recalculation.** Upstream recalculates only when some formula has no
   cached value, and LibreOffice then keeps any cached values it finds — a workbook with
   placeholder results (xlsxwriter caches 0 for every formula and sets `fullCalcOnLoad`)
   is graded on zeros. Atoll recalculates whenever a formula lacks a value *or* the file
   asks to be recalculated on open, and always fully (`atoll_tools.office`).
2. **A failed recalculation aborts grading** instead of falling back to cached values.
3. **Every non-General number format is shown**, not only currency/percent/grouped ones:
   precision requirements (two decimals) are judged by the format.

**Large sheets** (more than 500 non-empty rows) are shown as their first 30 and last 5
rows plus a program-computed `【数据概要】` over every data row: row count, distinct and
duplicate values, value counts of low-cardinality columns, column sums, min/max, formula
counts and error values (total rows excluded and named). Rows the rubric item names (by
code or key) are rendered in full, with their formulas, just before the item. Counts,
totals, uniqueness and coverage are therefore judged on all rows, not on a visible
sample.

**Formula errors** count when a cell shows an error value, or when its formula text is
not valid Excel (natural language after `=`, full-width operators, `Sheet.A1`
references, missing operands) — even if the error only appears after recalculation. An
error that appears only after recalculating a *valid* formula is treated as a
LibreOffice incompatibility and does not count.

## Containers

`scripts/run_grade.sh` grades with the `atoll-runner` image (`atoll-grade` entrypoint;
LibreOffice is installed there, as in Archipelago's environment image — no model-facing
tool reaches it unless a run opts into the [office tools](../native_tools/README.en.md#highlight-the-libreoffice-office-layer)):

- **render** — parses the untrusted, agent-written files: `--network none`, no
  credentials, task and run mounted read-only.
- **judge** — reads only `rendered.json`; its sole network is a per-grade Docker
  `--internal` network whose exit is an egress proxy admitting the judge endpoint
  (`$ATOLL_EGRESS_ALLOW`, else the `LITELLM_BASE_URL` host).

Both run with the eval containers' locks (read-only root, no capabilities, non-root,
resource caps, no Docker socket).

## Scores

`score.json` per run:

| Field | Meaning |
|---|---|
| `score` | **the score** — weighted percentage: (Σ weights of passed credit items − Σ \|weights\| of triggered penalties) ÷ Σ credit weights, clamped to 0–100; 0 if a veto fires |
| `vetoed` | a weight-0 veto item fired |
| `items` | per item: `rubric_number`, `rubric_weight`, `verdict`, `rationale` (中文), and for penalties `deduction_for`, `reference_has_error` |
| `usage` | judge calls and tokens |

Per model: **`mean_score`** = the mean of `score` over its runs (one run by default).
`atoll-grade --summarize <task_dir>` writes it to `<task_dir>/results/summary.json`, from
every `results/<model>/run_<n>/score.json` (each model's runs with their `score`, plus
`mean_score`).

## How this differs from Mercor's grading

- **The judge always sees the reference (golden) outputs.** In the APEX-Agents setup the
  judge *"takes in the task prompt, the agent output, a log of the changes induced by the
  agent's actions"* (§4.2); Archipelago's grader can attach reference files to a
  criterion (`artifacts_to_reference`), but only where a criterion is configured to.
  Atoll's tasks come with expert-prepared reference answers, so every item gets them:
  they settle open-ended and judgment items far more reliably and make that preparation
  count. Rubric items should still be self-contained where they can be.
- **One weighted score.** Archipelago reports the unweighted share of criteria passed
  (and Pass@1). Atoll's rubrics weight items deliberately — key judgment steps carry more
  weight than routine checks, penalties subtract, a veto zeroes the run — so Atoll reports
  the weighted percentage only.
- **The rubric is the whole grade.** No deterministic checks beside the judge — as in
  APEX-Agents, where every criterion is judged by the LLM (§4.2); Archipelago's other
  verifier types serve other benchmarks.
- **Same extractor, recalculated strictly, locally.** Atoll renders with Archipelago's own
  extraction code but forces full LibreOffice recalculation and never falls back to stale
  values; it does not use the remote parsing service (Reducto) upstream can call, so task
  files never leave the machine. A judge reading cached values grades what was last
  *saved*, not what a user sees: formula-only workbooks, placeholder caches and formulas
  invalid in Excel would all be misjudged.
- **The judge model is yours to choose.** APEX-Agents uses Gemini 3 Flash (*"we use
  Gemini 3 Flash, with thinking set to low, as the judge."*,
  [arXiv:2601.14242](https://arxiv.org/abs/2601.14242), §4.2). Pick a judge for your
  rubrics' workload: long rubrics with numeric checks over multi-sheet workbooks call for a
  strong reasoning model.

## Difficulty levels

`L1` (easiest) – `L2` – `L3` (hardest), assigned from the per-model mean scores.
Thresholds are **tentative** and live in config, not code.

## Code map

| Path | Role |
|---|---|
| `atoll_grading/inputs.py` | task and run loaders (both layouts), snapshot diff |
| `atoll_grading/render.py` | vendored extractor + Atoll patches, large-sheet summaries, named rows |
| `atoll_grading/judge.py` | prompts, per-item calls, verdict parsing |
| `atoll_grading/score.py` | the weighted fold, per-model `mean_score` |
| `atoll_grading/main.py` | the `atoll-grade` CLI |
| `prompts/` | judge system prompt + per-item user message |
| `tests/` | inputs, rendering (LibreOffice tests skip without `soffice`), parsing, fold |

## TODO

- [ ] Calibrate the L1–L3 thresholds on the existing task set (~100 tasks) — on scores
      re-graded with this judge.
- [ ] Chart images: the extractor can render charts to images (LibreOffice → PDF →
      poppler); the judge is text-only for now.
