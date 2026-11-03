# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""pdf_server: native port of Archipelago's pdf_server MCP tools.

PDF processing in a sandboxed directory: extract text and images, search text,
render pages as images, detect strikethrough, and create PDFs from structured
blocks (paragraphs, headings, lists, tables, page breaks). Use for document
analysis, report generation, and search.

Importing this package registers the five tools (create_pdf, read_pdf_pages,
read_image, read_page_as_image, search_pdf) under the ``pdf_server`` family.
Divergences from upstream are documented per module; the model-visible surface
(schemas, docstrings, output text, error strings) is unchanged.
"""

# Serve the model-visible schemas exactly as Archipelago's gateway published
# them at the pinned upstream commit (see atoll_tools.published).
from pathlib import Path as _Path

from atoll_tools.published import attach_published_schemas as _attach
from atoll_tools.registry import SERVERS as _SERVERS

from pdf_server import (  # noqa: F401
    create_pdf,
    read_image,
    read_page_as_image,
    read_pdf_pages,
    search_pdf,
)

_attach(_Path(__file__).with_name("published_schemas.json"), _SERVERS["pdf_server"])
