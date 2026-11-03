"""Agent registration (mirrors Archipelago's agents/runner/agents/registry.py)."""

from runner.loop import LoopTruncatedToolsAgent

AGENTS: dict[str, type] = {
    "loop_truncated_tools_agent": LoopTruncatedToolsAgent,
    # Back-compat alias for the previous simplified loop's registry key.
    "loop": LoopTruncatedToolsAgent,
}


def get_agent(name: str) -> type:
    """Look up an agent class by its registered name."""
    try:
        return AGENTS[name]
    except KeyError:
        raise KeyError(f"unknown agent {name!r}; available: {sorted(AGENTS)}") from None
