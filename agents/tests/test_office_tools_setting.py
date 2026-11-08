"""The runner's office_tools switch: CLI > task.toml [atoll].office_tools > $ATOLL_OFFICE_TOOLS > off."""

import pytest
from runner.main import resolve_office_tools


def test_default_off(monkeypatch):
    monkeypatch.delenv("ATOLL_OFFICE_TOOLS", raising=False)
    assert resolve_office_tools({}, None) is False


def test_precedence(monkeypatch):
    monkeypatch.setenv("ATOLL_OFFICE_TOOLS", "on")
    assert resolve_office_tools({}, None) is True
    assert resolve_office_tools({"office_tools": False}, None) is False  # task beats env
    assert resolve_office_tools({"office_tools": False}, "on") is True  # CLI beats task
    assert resolve_office_tools({"office_tools": "yes"}, None) is True


def test_invalid(monkeypatch):
    monkeypatch.delenv("ATOLL_OFFICE_TOOLS", raising=False)
    with pytest.raises(ValueError, match="office_tools"):
        resolve_office_tools({"office_tools": "maybe"}, None)
