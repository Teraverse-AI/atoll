# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""read_image tool (port of tools/read_image.py).

Divergences: fastmcp's ``Image`` return type maps to Atoll's ``ImageContent``
(the MCP image-block equivalent); the cache lives in the Atoll process instead
of an MCP server process. Upstream serves only cached images — a key unknown
to the cache gets the same "call read_document_content first" error. Error
messages are unchanged.
"""

import base64
from typing import Annotated

from atoll_tools.content import ImageContent
from atoll_tools.registry import register
from pydantic import Field

from docs_server._image_cache import IMAGE_CACHE


@register("docs_server")
def read_image(
    file_path: Annotated[
        str,
        Field(
            description="Absolute path to the .docx file containing the image (e.g., '/documents/report.docx')"
        ),
    ],
    annotation: Annotated[
        str,
        Field(
            description="Annotation key from ImageRun in read_document_content output (e.g., 'body_p_0_r0'); the '@' prefix is optional"
        ),
    ],
) -> ImageContent:
    """Read an image from document using file path and annotation key."""
    if not isinstance(file_path, str) or not file_path:
        raise ValueError("File path is required and must be a string")

    if not isinstance(annotation, str) or not annotation:
        raise ValueError("Annotation is required and must be a string")

    # Normalize path to match read_document_content behavior (must start with /)
    if not file_path.startswith("/"):
        file_path = "/" + file_path

    # Strip leading @ if present (the @ is a display prefix in read_document_content output)
    clean_annotation = annotation.lstrip("@")

    # Validate annotation is not empty after stripping
    if not clean_annotation:
        raise ValueError("Annotation cannot be empty or contain only '@' characters")

    cache_key = f"{file_path}::{clean_annotation}"

    try:
        # Use get() atomically to avoid race condition between check and get
        base64_data = IMAGE_CACHE.get(cache_key)

        if base64_data is None:
            raise ValueError(
                f"Image not found in cache for file '{file_path}' with annotation '{annotation}'. "
                "Make sure you've called read_document_content first to extract images."
            )

        if len(base64_data) == 0:
            raise ValueError("Image data is empty")

        image_bytes = base64.b64decode(base64_data)
        return ImageContent.from_bytes(image_bytes, "image/jpeg")

    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to read image from cache: {repr(exc)}") from exc
