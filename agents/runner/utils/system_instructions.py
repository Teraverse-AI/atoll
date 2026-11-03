# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""No-op shim for upstream ``runner/utils/system_instructions.py``.

Upstream checks a harness's first LLM request against its declared
``system_instructions_placement`` and emits a metric/warning on mismatch.
Atoll's loop does not declare placements (the upstream check context is never
armed, so even upstream's own implementation would return immediately); the
shim keeps the vendored call site in ``runner/utils/llm.py`` intact without
dragging the agent-descriptor machinery.
"""

from typing import Any


def check_first_request(request_messages: list[Any], harness: Any) -> None:
    """Upstream signature; the placement check context is never armed in Atoll."""
    del request_messages, harness
