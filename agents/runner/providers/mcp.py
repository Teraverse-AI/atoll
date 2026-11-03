"""MCP tool provider — stub for future Archipelago gateway interop.

Not implemented yet by design: Atoll's target machines cannot run the MCP server
fleet. The interface is kept so a run can switch providers without touching the loop.
"""

from typing import Any

from runner.providers.base import ToolProvider


class McpToolProvider(ToolProvider):
    """JSON-RPC over stdio/HTTP to an MCP gateway (planned, unimplemented)."""

    def __init__(self, url: str, auth_token: str | None = None) -> None:
        self.url = url
        self.auth_token = auth_token

    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError("MCP interop is planned; use NativeToolProvider")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("MCP interop is planned; use NativeToolProvider")
