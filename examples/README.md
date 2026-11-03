# examples

Example Harbor-style task bundles, as in Archipelago's `examples/` — a small
public sample, not the full benchmark task set. Each task directory:

```
qXXX_name/
├── task.toml          # metadata + agent stop conditions + [atoll] config + verifiers
├── instruction.md     # the prompt the agent receives
├── workspace/         # files copied into the agent's run workspace (read-only template)
└── gold_answer.json   # expected output, consumed by grading (deferred)
```

Binary fixtures (`.xlsx`, `.pdf`) are generated, not hand-edited:

```bash
uv run python scripts/generate_task_workspaces.py
```

| Task | Topic | Difficulty |
| --- | --- | --- |
| `q001_adjusting_entry` | Year-end prepaid insurance adjustment | easy |
| `q002_bank_reconciliation` | Monthly bank reconciliation | medium |
