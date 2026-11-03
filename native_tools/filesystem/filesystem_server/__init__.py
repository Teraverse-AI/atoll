# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""filesystem_server: Archipelago's filesystem MCP server as native Atoll tools.

Read-only access to a sandboxed directory. List and search files, read text or
image files, get file or directory metadata, and get a directory tree. No
create, modify, or delete.

Importing this package imports every tool module so each
``@register("filesystem_server")`` decorator runs. Upstream's ``eml_reader`` is
not a registered tool (only a helper of ``read_text_file``) and stays private
as ``_eml``.
"""

# Serve the model-visible schemas exactly as Archipelago's gateway published
# them at the pinned upstream commit (see atoll_tools.published).
from pathlib import Path as _Path

from atoll_tools.published import attach_published_schemas as _attach
from atoll_tools.registry import SERVERS as _SERVERS

from filesystem_server import (  # noqa: F401
    get_directory_tree,
    get_file_metadata,
    list_files,
    read_image_file,
    read_text_file,
    search_files,
)

_attach(_Path(__file__).with_name("published_schemas.json"), _SERVERS["filesystem_server"])
