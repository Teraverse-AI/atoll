"""The served tool surface must equal the vendored published schemas exactly.

The ``published_schemas.json`` next to each server package is the JSON that
Archipelago's gateway served from ``tools/list`` at the pinned upstream commit
(captured with each server's locked fastmcp version). These tests pin Atoll's
model-visible surface to those artifacts byte-for-byte.
"""

import json
from pathlib import Path

import pytest
from atoll_tools.registry import load_tools
from atoll_tools.tool_schema import registry_to_openai_schemas

NATIVE_TOOLS = Path(__file__).resolve().parents[2]

SERVERS = {
    "sheets_server": "spreadsheets/sheets_server",
    "filesystem_server": "filesystem/filesystem_server",
    "pdf_server": "pdfs/pdf_server",
    "code_execution_server": "code/code_execution_server",
    "docs_server": "documents/docs_server",
}


@pytest.mark.parametrize("server", sorted(SERVERS))
def test_served_schema_matches_published(server):
    golden = json.loads((NATIVE_TOOLS / SERVERS[server] / "published_schemas.json").read_text())
    served = {
        s["function"]["name"]: s["function"]
        for s in registry_to_openai_schemas(load_tools([server]))
    }
    assert served.keys() == golden.keys()
    for name, fn in served.items():
        assert fn["parameters"] == golden[name]["parameters"], name
        if golden[name]["description"] is not None:
            assert fn["description"] == golden[name]["description"], name


def test_enveloped_tools_accept_flat_arguments():
    """The EnvelopeCompatMiddleware port: flat args wrap into the input object."""
    from atoll_tools.published import wrap_flat_arguments

    tools = load_tools(["docs_server"])
    enveloped = tools["page_margins"]
    wrapped = wrap_flat_arguments(enveloped, {"file_path": "/r.docx", "action": "read"})
    assert wrapped == {"input": {"file_path": "/r.docx", "action": "read"}}
    # already-enveloped calls pass through untouched
    args = {"input": {"file_path": "/r.docx", "action": "read"}}
    assert wrap_flat_arguments(enveloped, args) is args
    # flat tools pass through untouched
    flat = tools["delete_document"]
    args = {"file_path": "/r.docx"}
    assert wrap_flat_arguments(flat, args) is args
