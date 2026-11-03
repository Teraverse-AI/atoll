# core

Atoll's own plumbing — the one module here with no Archipelago counterpart. Every
tool server registers through it; the gateway naming rule it applies is what makes
the mounted surface look like Archipelago's.

Package: `atoll_tools/` · Tests: `tests/`.

- `registry.py` — `@register("<server>")`; `load_tools([...])` applies the FastMCP
  gateway naming rule (one server mounted → `{tool_name}`; several →
  `{server_name}_{tool_name}`).
- `tool_schema.py` — serves a tool's vendored published schema verbatim when one is
  stamped (see `published.py`); otherwise introspects signatures
  (`Annotated[T, Field(description=...)]` or Google-style `Args:`) into OpenAI
  function-calling schemas.
- `published.py` — loads each server's `published_schemas.json` (the exact
  `tools/list` JSON Archipelago's gateway served at the pinned upstream commit,
  captured with each server's locked fastmcp version) and stamps it onto the tool
  functions; also ports upstream's EnvelopeCompatMiddleware, wrapping flat
  arguments into the nested `input` object the published schema of a
  single-model tool expects.
- `context.py` — the per-run workspace root; all paths resolve inside it, traversal
  out is rejected.
- `content.py` — `ImageContent`, the MCP image-block equivalent for tools that
  return images.
