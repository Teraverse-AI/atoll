# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""No-op shim for upstream ``runner/utils/budget_hydration.py``.

Upstream asks its orchestration server to rebuild a lost Redis budget meter.
Atoll has no server and no Redis, so hydration can never be needed: the shim
``runner/utils/budget_meter.py`` never reports a stale meter, and this
callback is only ever passed, never awaited.
"""


async def request_meter_hydration(budget_unit: str) -> None:
    """Upstream signature; nothing to hydrate without a server-side meter."""
    del budget_unit
