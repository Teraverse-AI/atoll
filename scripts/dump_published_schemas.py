"""Regenerate native_tools/*/published_schemas.json from an upstream checkout.

Each ported server vendors the exact ``tools/list`` JSON Archipelago's gateway
served at the pinned upstream commit (see agents/runner/utils/PATCHES.md for
the commit). This script replicates each server's ``main.py`` schema pipeline
against the upstream sources, using the fastmcp version that server's
``uv.lock`` pins — the pipelines behave differently across fastmcp versions
(sheets' flatten persists on 3.2.4; 3.3.x needs the canonical-mutation form).

Usage (per server, inside a venv with that server's locked fastmcp):
    python scripts/dump_published_schemas.py <archipelago-dir> documents
    python scripts/dump_published_schemas.py <archipelago-dir> spreadsheets  # fastmcp 3.2.4
    python scripts/dump_published_schemas.py <archipelago-dir> filesystem|pdfs|code

Stub modules for loguru/asyncer/mcp_actor must be first on PYTHONPATH (the
upstream tool modules import them at module level); 10-line no-op stubs
suffice. Output goes to stdout; python_code_execution's description must be
set to null afterwards (upstream builds it dynamically from the sandbox
package manifest — Atoll keeps its docstring for that one tool).

After regenerating, `uv run pytest native_tools/core` pins the served surface
against the new artifacts.
"""

import asyncio
import importlib
import json
import os
import sys

ARCH = sys.argv[1].rstrip("/") + "/mcp_servers"
server = sys.argv[2]

CONF = {
    "documents": dict(
        pkg="docs_server",
        tools=[
            "create_document", "delete_document", "get_document_overview",
            "read_document_content", "read_image", "add_content_text",
            "edit_content_text", "delete_content_text", "add_image",
            "modify_image", "apply_formatting", "header_footer",
            "page_margins", "page_orientation", "comments",
        ],
        pipeline="docs",
    ),
    "spreadsheets": dict(
        pkg="sheets_server",
        tools=[
            "create_spreadsheet", "delete_spreadsheet", "read_tab", "read_csv",
            "list_tabs_in_spreadsheet", "add_tab", "delete_tab",
            "edit_spreadsheet", "add_content_text", "delete_content_cell",
            "create_chart", "filter_tab",
        ],
        pipeline="sheets",
    ),
    "filesystem": dict(
        pkg="filesystem_server",
        tools=[
            "list_files", "read_text_file", "read_image_file", "search_files",
            "get_file_metadata", "get_directory_tree",
        ],
        pipeline="canonical_flatten",
    ),
    "pdfs": dict(
        pkg="pdf_server",
        tools=[
            "read_pdf_pages", "search_pdf", "create_pdf", "read_image",
            "read_page_as_image",
        ],
        pipeline="canonical_flatten",
    ),
    "code": dict(
        pkg="code_execution_server",
        tools=[("python_exec", "python_code_execution"), ("code_exec", "code_exec")],
        pipeline="canonical_flatten",
    ),
}[server]

base = f"{ARCH}/{server}/mcp_servers/{CONF['pkg']}"
shared = f"{ARCH}/{server}/packages/mercor-mcp-shared/packages/mcp_schema"
if not os.path.isdir(shared):
    shared = f"{ARCH}/{server}/packages/mcp_schema"
sys.path[:0] = [base, shared]

from fastmcp import FastMCP  # noqa: E402
from mcp_schema import flatten_schema  # noqa: E402

mcp = FastMCP(f"{server}-server")
for t in CONF["tools"]:
    mod_name, fn_name = t if isinstance(t, tuple) else (t, t)
    mod = importlib.import_module(f"tools.{mod_name}")
    mcp.tool(getattr(mod, fn_name))


async def main() -> None:
    if CONF["pipeline"] == "docs":
        from utils.schema_unions import merge_object_unions

        for tool in await mcp.list_tools():
            canonical = await mcp.get_tool(tool.name)
            params = getattr(canonical, "parameters", None)
            if isinstance(params, dict):
                flattened = flatten_schema(merge_object_unions(params))
                params.clear()
                params.update(flattened)
    elif CONF["pipeline"] == "canonical_flatten":
        for tool in await mcp.list_tools():
            canonical = await mcp.get_tool(tool.name)
            params = getattr(canonical, "parameters", None)
            if isinstance(params, dict):
                flattened = flatten_schema(params)
                params.clear()
                params.update(flattened)
    elif CONF["pipeline"] == "sheets":
        # sheets main.py mutates the live tools list_tools() returns (fastmcp 3.2.4)
        for tool in await mcp.list_tools():
            params = tool.parameters
            if isinstance(params, dict):
                tool.parameters = flatten_schema(params)

    out = {}
    for tool in await mcp.list_tools():
        m = tool.to_mcp_tool()
        out[tool.name] = {"description": m.description, "parameters": m.inputSchema}
    print(json.dumps(out, indent=1))


asyncio.run(main())
