# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Shim for upstream ``runner/utils/mcp.py``.

Atoll's tools come from an in-process ToolProvider, so the MCP session/gateway
machinery (FastMCP client, transports, reconnection) is not vendored. The
vendored ``runner/utils/llm.py`` imports exactly one symbol from this module —
``tool_accepts_extra_headers`` — whose pure schema-inspection implementation
(and its two helpers) is copied verbatim from upstream so the behavior is
identical. ``UnsupportedRequestFieldError`` rides along for API parity.
"""

from typing import Any


def resolve_local_schema_ref(schema: Any, root: dict[str, Any]) -> Any:
    """Resolve a local ``$ref`` against the root schema's ``$defs``.

    FastMCP advertises Pydantic request models as
    ``properties.request: {$ref: "#/$defs/SomeRunRequest"}``. Without following
    that, the wrapper has no ``properties`` / ``additionalProperties`` and every
    capability check below fails closed on a real gateway schema.
    """
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    node: Any = root
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return schema
        node = node[part]
    return node if isinstance(node, dict) else schema


class UnsupportedRequestFieldError(Exception):
    """Raised when a pinned service build lacks a field a run needs.

    A platform version pins a service commit that never ages out, so a
    capability the caller needs can simply be absent. Dropping that field
    would change what the run measures.
    """


def tool_request_schema(tool: Any) -> dict[str, Any] | None:
    """The advertised schema of a tool's ``request`` object, or None.

    Falls back to the tool's own input schema for tools that take their fields
    flat rather than nested under ``request``.
    """
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    if not isinstance(schema, dict) or not schema:
        return None
    props = schema.get("properties")
    target = (
        props["request"]
        if isinstance(props, dict) and isinstance(props.get("request"), dict)
        else schema
    )
    target = resolve_local_schema_ref(target, schema)
    return target if isinstance(target, dict) else None


def tool_accepts_extra_headers(tool: Any) -> bool:
    """True when this build of ``tool`` will take an ``extra_headers`` key.

    Wider than declared-field checks on purpose: an app that spreads unknown
    keys (``extra="allow"``) takes the field without declaring it.
    ``additionalProperties: false`` (``extra="forbid"``) accepts it only when
    declared; ``true``, a nested schema, or an omitted value (the JSON Schema
    default) all accept it.
    """
    target = tool_request_schema(tool)
    if target is None:
        return False
    target_props = target.get("properties")
    if isinstance(target_props, dict) and "extra_headers" in target_props:
        return True
    additional = target.get("additionalProperties")
    if additional is False:
        return False
    if additional is True or isinstance(additional, dict):
        return True
    # additionalProperties omitted: JSON Schema allows extras. Require a
    # declared request shape so an empty fake schema does not opt in.
    return bool(target_props)
