# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""Identifier tokens and constants (port of helpers/constants.py). Divergence: none."""

DOCX_CONTENT_TYPE: str = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)

# Identifier tokens used in read_document_content/set_text_content
TOKEN_BODY: str = "body"
TOKEN_HEADER: str = "header"
TOKEN_FOOTER: str = "footer"
TOKEN_SECTION: str = "s"
TOKEN_TABLE: str = "tbl"
TOKEN_ROW: str = "r"
TOKEN_CELL: str = "c"
TOKEN_PARAGRAPH: str = "p"
