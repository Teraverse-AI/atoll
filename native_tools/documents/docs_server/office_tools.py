"""Atoll opt-in office tool for docs_server (not part of Archipelago's surface).

Registered with ``register_optin``: mounted only when a run turns office tools on
(``--office-tools on`` / task.toml ``[atoll] office_tools`` / ``$ATOLL_OFFICE_TOOLS``).
"""

import os
import shutil
from typing import Annotated

from atoll_tools.office import OfficeError, convert
from atoll_tools.registry import register_optin
from pydantic import Field

from docs_server._paths import PathTraversalError, resolve_under_root


@register_optin("docs_server")
def convert_document_to_pdf(
    file_path: Annotated[
        str,
        Field(
            description="Absolute path to the .docx file to convert, starting with '/' (e.g., '/reports/memo.docx')"
        ),
    ],
    output_path: Annotated[
        str | None,
        Field(
            description="Absolute path for the .pdf to write, starting with '/'; defaults to the document's path with a .pdf extension"
        ),
    ] = None,
) -> str:
    """Convert a .docx document to PDF with LibreOffice, e.g. to check its pagination and layout as printed."""

    if not isinstance(file_path, str) or not file_path:
        return "File path is required"
    if not file_path.startswith("/"):
        return "File path must start with /"
    if not file_path.lower().endswith(".docx"):
        return "File path must end with .docx"
    pdf_path = output_path or file_path[: -len(".docx")] + ".pdf"
    if not pdf_path.startswith("/") or not pdf_path.lower().endswith(".pdf"):
        return "Output path must start with / and end with .pdf"
    try:
        source = resolve_under_root(file_path)
        target = resolve_under_root(pdf_path)
    except PathTraversalError:
        return f"Invalid path: {output_path or file_path}"
    if not os.path.isfile(source):
        return f"File not found: {file_path}"

    try:
        produced = convert(source, "pdf")
    except OfficeError as exc:
        return f"Failed to convert document: {exc}"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(produced, target)
    except Exception as exc:
        return f"Failed to convert document: {repr(exc)}"
    finally:
        shutil.rmtree(produced.parent, ignore_errors=True)
    return f"Converted {file_path} to {pdf_path}."
