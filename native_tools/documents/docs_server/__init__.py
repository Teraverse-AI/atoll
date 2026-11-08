# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""docs_server: Word (.docx) documents in a sandboxed directory.

Native port of Archipelago's mcp_servers/documents docs_server
(expanded/individual-tools mode, 15 tools). Importing this package imports
every tool module so their ``@register("docs_server")`` decorators run.

Divergences from upstream (each tool module documents its own):
- fastmcp/loguru/asyncio infrastructure stripped; tools are plain sync
  functions returning the same text upstream's text block carried.
- Model-visible schemas are served verbatim from the vendored
  ``published_schemas.json`` (the gateway's ``tools/list`` output at the pinned
  upstream commit); single-pydantic-model tools keep upstream's nested
  ``input`` envelope, and flat calls are wrapped like upstream's
  EnvelopeCompatMiddleware does.
- Paths resolve against Atoll's per-run workspace root instead of
  mcp_actor's per-actor filesystem root.
"""

# Serve the model-visible schemas exactly as Archipelago's gateway published
# them at the pinned upstream commit (see atoll_tools.published).
from pathlib import Path as _Path

from atoll_tools.published import attach_published_schemas as _attach
from atoll_tools.registry import SERVERS as _SERVERS

from docs_server import (  # noqa: F401
    add_content_text,
    add_image,
    apply_formatting,
    comments,
    create_document,
    delete_content_text,
    delete_document,
    edit_content_text,
    get_document_overview,
    header_footer,
    modify_image,
    page_margins,
    page_orientation,
    read_document_content,
    read_image,
)

_attach(_Path(__file__).with_name("published_schemas.json"), _SERVERS["docs_server"])

# Atoll opt-in office tools (registered apart from the ported surface; see atoll_tools.office).
from docs_server import office_tools as _office_tools  # noqa: E402, F401
