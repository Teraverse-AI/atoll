from runner.providers.native import NativeToolProvider


def _registry():
    def add(a: int, b: int = 1) -> dict:
        """Add two integers.

        Args:
            a: First addend.
            b: Second addend.
        """
        return {"sum": a + b}

    def shout(word: str) -> str:
        """Return the word, loudly.

        Args:
            word: The word.
        """
        return word.upper()

    def boom() -> dict:
        """Always fails."""
        raise ValueError("kaboom")

    return {"add": add, "shout": shout, "boom": boom}


def test_list_tools_openai_schema():
    provider = NativeToolProvider(_registry())
    tools = {t["function"]["name"]: t for t in provider.list_tools()}
    assert tools["add"]["function"]["parameters"]["required"] == ["a"]


def test_call_tool_dict_str_and_errors():
    provider = NativeToolProvider(_registry())
    assert provider.call_tool("add", {"a": 2, "b": 3}) == {"sum": 5}
    assert provider.call_tool("shout", {"word": "hi"}) == "HI"  # str passes through verbatim
    assert provider.call_tool("nope", {}) == "Unknown tool: nope"
    # raised exceptions come back as MCP-gateway-style error text
    assert provider.call_tool("boom", {}) == "Error executing tool boom: kaboom"
    bad_args = provider.call_tool("add", {"wrong": 1})
    assert bad_args.startswith("Error executing tool add:")


def test_default_registry_and_workspace_binding(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    provider = NativeToolProvider(workspace_root=tmp_path)
    names = {t["function"]["name"] for t in provider.list_tools()}
    assert "filesystem_server_read_text_file" in names
    assert "code_execution_server_python_code_execution" in names
    result = provider.call_tool("filesystem_server_read_text_file", {"file_path": "/hello.txt"})
    assert isinstance(result, str)
    assert "hi" in result
