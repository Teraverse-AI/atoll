# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""filesystem_server.search_files as a native Atoll tool.

Divergences from upstream: pure sync (no make_async_background); returns the
plain text content block only (no StructuredText / structuredContent);
``resolve_in_workspace`` resolves symlinks up front, so a base path escaping
the workspace reports '[access denied: ...]' at resolution time. Symlinked
result files are still containment-checked individually, as upstream does.
Model-visible text, schema, and description are identical.
"""

import fnmatch
import os
from typing import Annotated

from atoll_tools.context import PathTraversalError, resolve_in_workspace
from atoll_tools.registry import register
from pydantic import Field

from filesystem_server._paths import is_within_workspace, virtual_path


@register("filesystem_server")
def search_files(
    pattern: Annotated[
        str,
        Field(
            description="Glob pattern to match filenames (not full paths). Uses shell-style wildcards: '*' matches any characters, '?' matches single character, '[seq]' matches any character in seq. Does NOT support '**' for recursive directory matching (use 'recursive' parameter instead). Examples: '*.json' (all JSON files), 'report_*.csv' (CSV files starting with 'report_'), 'data_?.txt' (data_1.txt, data_a.txt, etc.). Returns a string with format: 'Found N file(s) matching 'pattern':\\n' followed by newline-separated absolute paths within the sandbox. If no matches: 'No files matching 'pattern' found in path'. Error cases return bracketed messages: '[not found: path]', '[access denied: path]', '[not a directory: path]', '[permission denied: path]'."
        ),
    ],
    path: Annotated[
        str,
        Field(
            description="Directory path within the sandbox to search in. Must start with '/'. Default: '/' (sandbox root). Example: '/documents' or '/data/exports'"
        ),
    ] = "/",
    recursive: Annotated[
        bool,
        Field(
            description="Search recursively through all subdirectories. Default: true. Note: Symlinks pointing outside the sandbox are skipped for security."
        ),
    ] = True,
    max_results: Annotated[
        int,
        Field(
            description="Maximum number of matching files to return. Default: 100. Set to 0 for unlimited results. When the limit is reached, results are truncated and a '(Results limited to N)' message is appended."
        ),
    ] = 100,
) -> str:
    """Search for files matching a glob pattern in the given directory. Uses glob syntax (not regex): * matches any chars, ? matches one char."""
    if not isinstance(pattern, str) or not pattern:
        raise ValueError("Pattern is required and must be a string")

    if not isinstance(path, str) or not path:
        raise ValueError("Path is required and must be a string")

    if not path.startswith("/"):
        raise ValueError("Path must start with /")

    try:
        base = resolve_in_workspace(path)
    except PathTraversalError:
        return f"[access denied: {path}]"

    if not os.path.lexists(base):
        return f"[not found: {path}]"

    # Check if it's actually a directory (use realpath for accurate check)
    real_base = os.path.realpath(base)
    if not os.path.isdir(real_base):
        return f"[not a directory: {path}]"

    matches: list[str] = []
    count = 0

    try:
        if recursive:
            # SECURITY: followlinks=False prevents symlink directory traversal escape
            for root, _dirs, files in os.walk(real_base, followlinks=False):
                for filename in files:
                    if fnmatch.fnmatch(filename, pattern):
                        full_path = os.path.join(root, filename)
                        # SECURITY: Skip files that are symlinks pointing outside sandbox
                        if os.path.islink(full_path) and not is_within_workspace(full_path):
                            continue
                        matches.append(virtual_path(full_path))
                        count += 1
                        if max_results > 0 and count >= max_results:
                            break
                if max_results > 0 and count >= max_results:
                    break
        else:
            with os.scandir(real_base) as entries:
                for entry in entries:
                    if not fnmatch.fnmatch(entry.name, pattern):
                        continue
                    # Check if it's a regular file (not following symlinks)
                    is_regular_file = entry.is_file(follow_symlinks=False)
                    # Check if it's a symlink to a file within sandbox
                    is_valid_symlink = False
                    if entry.is_symlink():
                        if is_within_workspace(entry.path):
                            # Symlink points inside sandbox - check if target is a file
                            try:
                                is_valid_symlink = entry.is_file(follow_symlinks=True)
                            except OSError:
                                # Broken symlink or permission error
                                is_valid_symlink = False

                    if is_regular_file or is_valid_symlink:
                        matches.append(virtual_path(entry.path))
                        count += 1
                        if max_results > 0 and count >= max_results:
                            break

    except PermissionError:
        return f"[permission denied: {path}]"
    except Exception as exc:
        return f"[error: {repr(exc)}]"

    if not matches:
        if path == "/":
            return (
                f"No files matching '{pattern}' found in {path}. "
                "The sandbox root may not contain files at the top level. "
                "Try searching in a subdirectory (e.g., '/documents', '/data')."
            )
        return f"No files matching '{pattern}' found in {path}"

    result = f"Found {len(matches)} file(s) matching '{pattern}':\n"
    result += "\n".join(matches)

    if max_results > 0 and count >= max_results:
        result += f"\n\n(Results limited to {max_results})"

    return result
