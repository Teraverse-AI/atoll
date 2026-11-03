"""Tool registry: the single source of truth mapping server -> tool name -> callable.

Tool families mirror Archipelago's MCP servers (filesystem_server, sheets_server,
pdf_server, code_execution_server, ...). The names a model observes follow
FastMCP's gateway rule, which Archipelago inherits:

- one server mounted:       ``{tool_name}``
- multiple servers mounted: ``{server_name}_{tool_name}``

so a run that mounts several families exposes e.g. ``filesystem_server_list_files``,
exactly like Mercor's gateway does.
"""

from collections.abc import Callable
from typing import Any

# A tool returns the text the model sees (str, upstream parity) or a
# JSON-serializable dict (serialized by the provider). It may raise: the provider
# renders exceptions as MCP-style error text.
ToolFn = Callable[..., Any]

SERVERS: dict[str, dict[str, ToolFn]] = {}


def register(server: str) -> Callable[[ToolFn], ToolFn]:
    """Register a tool function under a server (tool family) name.

    The function must carry type hints (``Annotated[..., Field(description=...)]``
    or a Google-style ``Args:`` docstring) — ``tool_schema.py`` introspects them.
    """

    def decorator(fn: ToolFn) -> ToolFn:
        family = SERVERS.setdefault(server, {})
        if fn.__name__ in family:
            raise ValueError(f"Duplicate tool name in {server}: {fn.__name__}")
        family[fn.__name__] = fn
        return fn

    return decorator


def _import_tool_modules() -> None:
    """Import all built-in server packages so their @register decorators run.

    Imports are local so that importing ``registry`` from a tool module never cycles.
    Each server lives in its own module folder under native_tools/ (mirroring
    Archipelago's mcp_servers/ layout) and installs as a top-level package.
    """
    import code_execution_server  # noqa: F401
    import docs_server  # noqa: F401
    import filesystem_server  # noqa: F401
    import pdf_server  # noqa: F401
    import sheets_server  # noqa: F401


def load_tools(servers: list[str] | None = None) -> dict[str, ToolFn]:
    """The flat registry for a run, FastMCP-named: prefixed iff several servers mount.

    Args:
        servers: Server names to mount (like the keys of an Archipelago
            mcp_config); None mounts every built-in server.
    """
    _import_tool_modules()
    selected = sorted(SERVERS) if servers is None else list(servers)
    unknown = [s for s in selected if s not in SERVERS]
    if unknown:
        raise KeyError(f"unknown tool server(s) {unknown}; available: {sorted(SERVERS)}")
    prefix = len(selected) > 1
    flat: dict[str, ToolFn] = {}
    for server in selected:
        for name, fn in SERVERS[server].items():
            flat[f"{server}_{name}" if prefix else name] = fn
    return flat


def load_default_tools() -> dict[str, ToolFn]:
    """All built-in servers, mounted together (gateway-prefixed names)."""
    return load_tools(None)
