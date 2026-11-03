from typing import Annotated

from atoll_tools.registry import load_default_tools, load_tools
from atoll_tools.tool_schema import tool_to_openai_schema
from pydantic import Field


def sample_tool(path: str, limit: int = 10, tags: list[str] | None = None) -> dict:
    """Do a sample thing.

    Args:
        path: Where to look.
        limit: How many results
            to return at most.
        tags: Optional tag filter.
    """
    return {}


def annotated_tool(
    file_path: Annotated[str, Field(description="Absolute path. Must start with '/'.")],
    max_size: Annotated[int, Field(description="Byte cap.")] = 0,
) -> str:
    """Read something."""
    return ""


def test_docstring_schema_shape():
    schema = tool_to_openai_schema(sample_tool)
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "sample_tool"
    assert fn["description"] == "Do a sample thing."
    props = fn["parameters"]["properties"]
    assert props["path"] == {"type": "string", "description": "Where to look."}
    assert props["limit"]["type"] == "integer"
    assert "to return at most" in props["limit"]["description"]  # continuation line joined
    assert props["tags"]["type"] == "array"
    assert fn["parameters"]["required"] == ["path"]


def test_annotated_field_schema_shape():
    schema = tool_to_openai_schema(annotated_tool, name="fs_annotated_tool")
    fn = schema["function"]
    assert fn["name"] == "fs_annotated_tool"  # registered (gateway) name wins
    props = fn["parameters"]["properties"]
    assert props["file_path"] == {
        "type": "string",
        "description": "Absolute path. Must start with '/'.",
    }
    assert props["max_size"]["type"] == "integer"
    assert fn["parameters"]["required"] == ["file_path"]


def test_gateway_naming_rule():
    # several servers mounted -> prefixed names (FastMCP rule, like Archipelago)
    multi = load_default_tools()
    assert "filesystem_server_list_files" in multi
    assert "sheets_server_read_tab" in multi
    assert "pdf_server_read_pdf_pages" in multi
    assert "code_execution_server_python_code_execution" in multi
    assert "docs_server_read_document_content" in multi
    # one server mounted -> bare names
    single = load_tools(["filesystem_server"])
    assert "list_files" in single
    assert "filesystem_server_list_files" not in single


def test_all_default_tools_produce_valid_schemas():
    registry = load_default_tools()
    for name, fn in registry.items():
        schema = tool_to_openai_schema(fn, name=name)
        assert schema["function"]["name"] == name
        assert schema["function"]["description"], f"{name} has no description"
        if getattr(fn, "__published_spec__", None) is not None:
            # Vendored published schemas are pinned verbatim by
            # test_published_parity; their internal shape is upstream's call.
            continue
        for pname, pschema in schema["function"]["parameters"]["properties"].items():
            assert "description" in pschema, f"{name}.{pname} missing description"
