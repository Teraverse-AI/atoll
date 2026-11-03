"""Convert Python function signatures into OpenAI function-calling schemas.

The wire format is identical to what an MCP gateway would expose — models cannot
tell native tools from MCP tools.
"""

import copy
import inspect
import re
import types
import typing
from collections.abc import Callable
from typing import Any

_PY_TO_JSON: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _field_description(annotation: Any) -> str | None:
    """The description from an ``Annotated[T, Field(description=...)]`` annotation."""
    if typing.get_origin(annotation) is not typing.Annotated:
        return None
    for meta in typing.get_args(annotation)[1:]:
        description = getattr(meta, "description", None)
        if isinstance(description, str):
            return description
        if isinstance(meta, str):
            return meta
    return None


def _annotation_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a type annotation to a JSON-schema fragment (best effort)."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {}

    if typing.get_origin(annotation) is typing.Annotated:
        return _annotation_to_schema(typing.get_args(annotation)[0])

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    # Optional[X] / X | None -> schema of X (optionality is carried by "required")
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _annotation_to_schema(non_none[0])
        return {}  # heterogeneous union: leave untyped

    if origin in (list, tuple, set):
        item = _annotation_to_schema(args[0]) if args else {}
        return {"type": "array", "items": item} if item else {"type": "array"}

    if origin is dict or annotation is dict:
        return {"type": "object"}

    if origin is typing.Literal:
        return {"enum": list(args)}

    if annotation in _PY_TO_JSON:
        return {"type": _PY_TO_JSON[annotation]}

    if annotation is list:
        return {"type": "array"}

    return {}


def _parse_docstring(doc: str | None) -> tuple[str, dict[str, str]]:
    """Split a Google-style docstring into (summary, {param: description})."""
    if not doc:
        return "", {}
    lines = inspect.cleandoc(doc).splitlines()

    summary_lines: list[str] = []
    for line in lines:
        if re.match(r"^\s*(Args|Returns|Raises|Examples?):\s*$", line):
            break
        summary_lines.append(line)
    summary = " ".join(s.strip() for s in summary_lines if s.strip())

    params: dict[str, str] = {}
    in_args = False
    current: str | None = None
    for line in lines:
        if re.match(r"^\s*Args:\s*$", line):
            in_args = True
            continue
        if in_args and re.match(r"^\s*(Returns|Raises|Examples?):\s*$", line):
            break
        if not in_args:
            continue
        match = re.match(r"^\s+(\*{0,2}\w+)(?:\s*\([^)]*\))?:\s*(.*)$", line)
        if match:
            current = match.group(1).lstrip("*")
            params[current] = match.group(2).strip()
        elif current and line.strip():
            params[current] += " " + line.strip()
    return summary, params


def tool_to_openai_schema(fn: Callable[..., Any], name: str | None = None) -> dict[str, Any]:
    """Build the OpenAI function-calling schema for a registered tool function.

    A tool stamped with a vendored published spec (``atoll_tools.published``)
    is served that spec verbatim — the exact schema Archipelago's gateway
    published at the pinned upstream commit. Otherwise the schema is
    introspected: parameter descriptions come from
    ``Annotated[T, Field(description=...)]`` metadata, falling back to a
    Google-style ``Args:`` docstring section.
    """
    spec = getattr(fn, "__published_spec__", None)
    if spec is not None:
        description = spec["description"]
        if description is None:  # upstream built it dynamically; use the docstring
            description, _ = _parse_docstring(fn.__doc__)
        return {
            "type": "function",
            "function": {
                "name": name or fn.__name__,
                "description": description,
                "parameters": copy.deepcopy(spec["parameters"]),
            },
        }

    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn, include_extras=True)
    summary, param_docs = _parse_docstring(fn.__doc__)

    properties: dict[str, Any] = {}
    required: list[str] = []
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        schema = _annotation_to_schema(annotation)
        description = _field_description(annotation) or param_docs.get(pname)
        if description:
            schema = {**schema, "description": description}
        properties[pname] = schema
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return {
        "type": "function",
        "function": {
            "name": name or fn.__name__,
            "description": summary,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def registry_to_openai_schemas(registry: dict[str, Callable[..., Any]]) -> list[dict[str, Any]]:
    """Schemas for every tool in a flat registry, under their registered (gateway) names."""
    return [tool_to_openai_schema(fn, name=name) for name, fn in sorted(registry.items())]
