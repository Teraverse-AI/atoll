"""Abstract tool provider.

``loop.py`` depends only on this interface — never on native_tools or MCP directly.
That boundary is what lets the same loop run against in-process functions today and
an Archipelago MCP gateway later.
"""

from abc import ABC, abstractmethod
from typing import Any


class ToolProvider(ABC):
    @abstractmethod
    def list_tools(self) -> list[dict[str, Any]]:
        """Return the available tools as OpenAI function-calling schemas."""
        ...

    @abstractmethod
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any] | str:
        """Execute a tool and return its observation.

        A ``str`` is delivered to the model verbatim (the text an MCP gateway
        would return, errors included); a ``dict`` is JSON-serialized.
        """
        ...
