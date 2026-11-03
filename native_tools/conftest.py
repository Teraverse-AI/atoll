import pytest
from atoll_tools.context import set_workspace_root


@pytest.fixture
def workspace(tmp_path):
    """A fresh workspace root, set as the active tool context."""
    set_workspace_root(tmp_path)
    return tmp_path
