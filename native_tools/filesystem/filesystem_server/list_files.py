# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""filesystem_server.list_files as a native Atoll tool.

Divergences from upstream: pure sync (no make_async_background); returns the
plain text content block only (no StructuredText / structuredContent);
``resolve_in_workspace`` resolves symlinks up front, so a symlink escaping the
workspace reports '[access denied: ...]' at resolution time. Model-visible
text, schema, and description are identical.
"""

import mimetypes
import os
from typing import Annotated

from atoll_tools.context import PathTraversalError, resolve_in_workspace
from atoll_tools.registry import register
from pydantic import Field


@register("filesystem_server")
def list_files(
    path: Annotated[
        str,
        Field(
            description="Absolute path within the sandbox filesystem to list. Must start with '/'. Accepts either a '/'-rooted sandbox path or a path as reported by this server's other tools. Default: '/' (sandbox root). Example: '/documents' or '/data/uploads'. Returns a newline-separated string where each line describes one entry: \"'name' (folder)\\n\" for directories, \"'name' (mime/type file) N bytes\\n\" for files. MIME type is guessed from extension ('unknown' if undetectable). Returns '[not found: path]', '[permission denied: path]', or '[not a directory: path]' for errors. Returns 'No items found' for empty directories."
        ),
    ] = "/",
) -> str:
    """List files and folders in a path; each entry shows name and type (file/folder). Use to browse a directory."""
    try:
        base = resolve_in_workspace(path)
    except PathTraversalError:
        return f"[access denied: {path}]\n"

    if not os.path.exists(base):
        return f"[not found: {path}]\n"
    if not os.path.isdir(base):
        return f"[not a directory: {path}]\n"

    items = ""
    try:
        with os.scandir(base) as entries:
            for entry in entries:
                if entry.is_dir():
                    items += f"'{entry.name}' (folder)\n"
                elif entry.is_file():
                    mimetype, _ = mimetypes.guess_type(entry.path)
                    stat_result = entry.stat()
                    items += (
                        f"'{entry.name}' ({mimetype or 'unknown'} file) "
                        f"{stat_result.st_size} bytes\n"
                    )
    except FileNotFoundError:
        return f"[not found: {path}]\n"
    except PermissionError:
        return f"[permission denied: {path}]\n"
    except NotADirectoryError:
        return f"[not a directory: {path}]\n"

    if not items:
        if not path or path == "/":
            items = (
                "Directory is empty. If you expected files here, use a more specific "
                "path (e.g., '/documents', '/data'). The root '/' maps to the sandbox "
                "root which may not contain files at the top level."
            )
        else:
            items = f"No items found in '{path}'"

    return items
