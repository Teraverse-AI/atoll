# system_prompts

Reference system prompts shipped with the runner.

| File | What it is |
|---|---|
| `atoll_agents.md` | **Atoll's default — a draft, edit freely.** Built on `apex_agents.md` (its core rules kept), extended with general answering rules: reading binary files with the provided tools, no package installs, saving deliverables under the exact requested names without touching inputs, keeping Excel formulas, and a final reply that states the answers and lists changed files. Diff the two to see every change. |
| `apex_agents.md` | **Reference only, never edit.** Mercor's APEX-Agents agent system prompt, verbatim: the paper states *"All agents use the same system prompt"* (Vidgen et al., 2026, [arXiv:2601.14242](https://arxiv.org/abs/2601.14242), Appendix D.2), and Archipelago's `agents/runner/agents/react_toolbelt_agent/README.md` ("Recommended System Prompt", pinned commit `f8e41cb`) gives the same text "to reproduce the results in the paper". Pinned by `agents/tests/test_system_prompt.py`; to run with it exactly, pass it as the global override (`--system-prompt-file agents/runner/system_prompts/apex_agents.md`). |

Archipelago keeps this prompt in docs, not in task data or harness code: the caller
puts it into the run's initial messages. Atoll does the same, with overrides
(per-task wins):

1. task.toml `[atoll] system_prompt_file` — path relative to the task dir
2. global: `atoll-run --system-prompt-file` (or `scripts/run_eval.sh --system-prompt-file`),
   else `$ATOLL_SYSTEM_PROMPT_FILE`
3. default: `atoll_agents.md`

Both prompts tell the model that `code_exec` sees the files under `/filesystem/` — the
sandbox mounts the workspace there, as Archipelago does.
