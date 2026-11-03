"""Serve each tool's model-visible schema exactly as Archipelago published it.

Archipelago's servers do not publish raw introspected signatures: FastMCP
builds a pydantic schema for each tool, then every server's ``main.py`` runs
upstream's ``flatten_schema`` over it (inlining ``$defs``/``anyOf``, stripping
defaults and titles, prefixing optional parameters with ``"(Optional) "`` and
marking them ``nullable``), and the documents server additionally merges
discriminated unions into one object node. Tools whose Python signature is a
single pydantic model are published with that one nested ``input`` object.

Rather than re-implementing that pipeline, each ported server vendors the
pipeline's *output* — the exact JSON served by ``tools/list`` at the pinned
upstream commit, captured with the server's locked fastmcp version — as
``published_schemas.json`` next to its package, and stamps it onto its tool
functions here. ``tool_schema.tool_to_openai_schema`` serves a stamped schema
verbatim; introspection remains the fallback for unstamped tools.

``wrap_flat_arguments`` is the client half of upstream's
``EnvelopeCompatMiddleware``: models usually call an ``input``-enveloped tool
with ``{"input": {...}}``, but when one sends the fields flat, Archipelago
wraps them back into the envelope before the tool runs — so Atoll does too.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

# Parameter names that act as a single-model envelope, as upstream's
# EnvelopeCompatMiddleware spells them.
_ENVELOPE_KEYS = ("input", "request")


def attach_published_schemas(json_path: str | Path, tools: dict[str, Callable[..., Any]]) -> None:
    """Stamp each tool in ``tools`` with its published spec from ``json_path``.

    The file maps bare tool name -> {"description": str | None, "parameters": dict}.
    A ``null`` description means "keep the introspected docstring" (used where
    upstream built the description dynamically, e.g. python_code_execution's
    package list). Every tool in the file must exist in ``tools`` and vice
    versa, so a port cannot silently drift from the vendored surface.
    """
    specs: dict[str, Any] = json.loads(Path(json_path).read_text())
    missing = specs.keys() ^ tools.keys()
    if missing:
        raise ValueError(f"{json_path}: tool set mismatch with registry: {sorted(missing)}")
    for name, spec in specs.items():
        tools[name].__published_spec__ = spec  # type: ignore[attr-defined]


def wrap_flat_arguments(fn: Callable[..., Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Wrap flat tool args into input/request when the schema expects a nested object.

    Port of upstream ``EnvelopeCompatMiddleware._wrap_flat_arguments``: when the
    published schema has exactly one property named ``input``/``request`` and
    the call does not already use it, the flat arguments become that property's
    value. Every other call passes through untouched.
    """
    spec = getattr(fn, "__published_spec__", None)
    if spec is None:
        return arguments
    properties = spec["parameters"].get("properties")
    if not isinstance(properties, dict):
        return arguments

    prop_keys = set(properties)
    for key in _ENVELOPE_KEYS:
        if key in arguments and key in prop_keys:
            return arguments

    if len(prop_keys) == 1:
        only = next(iter(prop_keys))
        if only in _ENVELOPE_KEYS and only not in arguments:
            return {only: arguments or {}}

    return arguments
