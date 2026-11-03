# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""No-op shim for upstream ``runner/utils/metrics.py``.

Upstream emits Datadog distributions/counts; Atoll vendors the callers
(``runner/utils/llm.py``, ``runner/utils/decorators.py``) byte-identical and
turns the telemetry OFF here instead of editing them out. Signatures match
upstream exactly; both upstream functions are documented fire-and-forget and
never-raising, so a silent no-op is the faithful "disabled" behavior.
"""


def increment(metric: str, tags: list[str] | None = None, value: int = 1) -> None:
    """Fire-and-forget COUNT submit. Disabled: no metrics backend in Atoll."""


def distribution(
    metric: str,
    value: float,
    tags: list[str] | None = None,
    *,
    include_run_dims: bool = True,
) -> None:
    """Fire-and-forget DISTRIBUTION submit. Disabled: no metrics backend in Atoll."""
