# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""No-op shim for upstream ``runner/utils/budget_meter.py``.

Upstream is a Redis-backed batch-spend guardrail that already degrades to a
no-op on a ``None`` client; it cannot be vendored verbatim because it imports
the ``redis`` package at module level, which Atoll does not ship. This shim
reproduces exactly the ``client is None`` behavior of every entry point the
vendored ``runner/utils/llm.py`` touches, with upstream signatures, so the
guardrail is vendored-but-disabled rather than subtracted. ``BudgetSnapshot``
and ``BudgetExceededError`` are copied verbatim from upstream (minus the
pydantic base — a plain dataclass carries the same fields).
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

LANE_TRAJECTORY = "trajectory"
LANE_GRADING = "grading"
LANE_SYNTH = "synth"
LANES: tuple[str, ...] = (LANE_TRAJECTORY, LANE_GRADING, LANE_SYNTH)

# The per-unit lane sets each workload passes (upstream values).
BATCH_LANES: tuple[str, ...] = (LANE_TRAJECTORY, LANE_GRADING)
SYNTH_LANES: tuple[str, ...] = (LANE_SYNTH,)


class BudgetExceededError(Exception):
    """Raised when a call is denied because the batch is over budget. Terminal."""

    def __init__(
        self,
        cost_unit: str,
        *,
        remaining_usd: float | None = None,
        model: str | None = None,
    ) -> None:
        self.cost_unit = cost_unit
        self.remaining_usd = remaining_usd
        self.model = model
        super().__init__(
            f"budget exceeded for {cost_unit} "
            f"(remaining={remaining_usd}, model={model})"
        )


@dataclass
class BudgetSnapshot:
    """Point-in-time meter read (USD); never produced here — ``read_state``
    always answers ``None`` (meter unavailable), the upstream fail-open state."""

    budget_total_usd: float | None = None
    remaining_usd: float | None = None
    costs_usd: dict[str, float] = field(default_factory=dict)
    untracked_est_usd: float = 0.0
    hydration_needed: bool = False


async def read_state(
    client: Any, unit: str, *, lanes: tuple[str, ...]
) -> BudgetSnapshot | None:
    """Upstream signature; ``None`` = meter unavailable, so the gate admits the call."""
    del client, unit, lanes
    return None


async def hydrate_if_stale(
    client: Any,
    unit: str,
    snap: BudgetSnapshot | None,
    request_hydration: Callable[[], Awaitable[None]],
) -> bool:
    """Upstream signature; nothing to repair without a meter."""
    del client, unit, snap, request_hydration
    return False


async def accrue(
    client: Any,
    unit: str,
    call_id: str,
    actual_usd: float,
    lane: str,
    untracked_est_usd: float = 0.0,
) -> None:
    """Upstream signature; spend accrual is disabled without a meter."""
    del client, unit, call_id, actual_usd, lane, untracked_est_usd
