# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""filesystem_server.read_image_file as a native Atoll tool.

Divergences from upstream: pure sync (no make_async_background); returns
``atoll_tools.content.ImageContent`` instead of fastmcp's ``Image`` (the loop
attaches it as an MCP-style image content block, so the model sees the same
thing); ``resolve_in_workspace`` resolves symlinks up front, so a symlink
escaping the workspace raises the same 'Access denied' ValueError as upstream's
real-path check. Model-visible schema and description are identical.
"""

import os
from typing import Annotated

from atoll_tools.content import ImageContent
from atoll_tools.context import PathTraversalError, resolve_in_workspace
from atoll_tools.registry import register
from pydantic import Field

_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


@register("filesystem_server")
def read_image_file(
    file_path: Annotated[
        str,
        Field(
            description="Absolute path to the image file within the sandbox filesystem. REQUIRED. Must start with '/'. Supported formats: PNG, JPG, JPEG, GIF, WEBP (case-insensitive). Example: '/images/screenshot.png' or '/uploads/photo.jpg'. Returns an Image object with 'data' (binary image content) and 'format' (string: 'png', 'jpeg', 'gif', or 'webp'). Raises FileNotFoundError if file doesn't exist, ValueError for unsupported formats or non-file paths, RuntimeError for read failures."
        ),
    ],
) -> ImageContent:
    """Read an image file and return it for vision APIs. Use to pass images to vision-capable agents."""
    if not isinstance(file_path, str) or not file_path:
        raise ValueError("File path is required and must be a string")

    if not file_path.startswith("/"):
        raise ValueError("File path must start with /")

    # Validate file extension
    file_ext = file_path.lower().split(".")[-1]
    if file_ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        raise ValueError(
            f"Unsupported image format: {file_ext}. Supported formats: png, jpg, jpeg, gif, webp"
        )

    try:
        real_path = resolve_in_workspace(file_path)
    except PathTraversalError as exc:
        raise ValueError(f"Access denied: {file_path}") from exc

    if not os.path.lexists(real_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    if not os.path.isfile(real_path):
        raise ValueError(f"Not a file: {file_path}")

    try:
        with open(real_path, "rb") as f:
            image_data = f.read()

        return ImageContent.from_bytes(image_data, _MIME_TYPES[file_ext])

    except Exception as exc:
        raise RuntimeError(f"Failed to read image file: {repr(exc)}") from exc
