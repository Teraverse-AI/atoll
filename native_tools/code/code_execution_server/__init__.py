"""code_execution_server: sandboxed code execution (mirrors Archipelago's server)."""

# Serve the model-visible schemas exactly as Archipelago's gateway published
# them at the pinned upstream commit (see atoll_tools.published).
from pathlib import Path as _Path

from atoll_tools.published import attach_published_schemas as _attach
from atoll_tools.registry import SERVERS as _SERVERS

from code_execution_server import code_exec, python_exec  # noqa: F401

_attach(_Path(__file__).with_name("published_schemas.json"), _SERVERS["code_execution_server"])
