"""Tool provider backed by the in-process native tool registry. Production default."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from runner.providers.base import ToolProvider


class NativeToolProvider(ToolProvider):
    """Calls ``registry[name](**arguments)`` directly — no processes, no sockets."""

    def __init__(
        self,
        registry: dict[str, Callable[..., dict[str, Any]]] | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        if registry is None:
            from atoll_tools.registry import load_default_tools

            registry = load_default_tools()
        self.registry = registry
        self._schemas: list[dict[str, Any]] | None = None
        if workspace_root is not None:
            from atoll_tools.context import set_workspace_root

            set_workspace_root(workspace_root)

    def list_tools(self) -> list[dict[str, Any]]:
        if self._schemas is None:
            from atoll_tools.tool_schema import registry_to_openai_schemas

            self._schemas = registry_to_openai_schemas(self.registry)
        return self._schemas

    def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Execute a tool. Returns the observation for the loop to deliver.

        A ``str`` is the text the model sees verbatim; a dict (or pydantic result
        model, e.g. python_code_execution's) is JSON-serialized; an image content
        object (``mime_type`` + ``base64_data``) is attached as an image. Errors —
        unknown tools, bad arguments, and exceptions tools raise — come back as
        MCP-gateway-style error text, matching what a model sees through
        Archipelago's FastMCP gateway.
        """
        fn = self.registry.get(name)
        if fn is None:
            return f"Unknown tool: {name}"
        try:
            from atoll_tools.published import wrap_flat_arguments

            # Envelope compat, as Archipelago's middleware does it: a tool whose
            # published schema nests everything under one "input" object also
            # accepts the fields sent flat.
            result = fn(**wrap_flat_arguments(fn, arguments))
        except Exception as exc:  # noqa: BLE001 — becomes a model-readable observation
            return f"Error executing tool {name}: {exc}"
        if hasattr(result, "mime_type") and hasattr(result, "base64_data"):
            return result  # image content: the loop attaches it to the conversation
        if hasattr(result, "model_dump"):  # pydantic result model
            return result.model_dump()
        if not isinstance(result, (dict, str)):
            return f"Error executing tool {name}: returned {type(result).__name__}, expected str or dict"
        return result
