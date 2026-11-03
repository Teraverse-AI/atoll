# Modified from Mercor-Intelligence/archipelago (Apache-2.0)
# https://github.com/Mercor-Intelligence/archipelago
"""add_content_text tool (port of tools/add_content_text.py).

Divergence: upstream annotates ``value`` as ``str | int | float | bool`` but
forces ``{"type": "string"}`` in the published schema via json_schema_extra;
here the parameter is annotated ``str`` so the model-visible schema matches,
while any runtime value is passed through to the cell unchanged.
"""

import os
import re
from io import BytesIO
from typing import Annotated

from atoll_tools.registry import register
from openpyxl import load_workbook
from pydantic import Field

from sheets_server._helpers import recalculate_formulas
from sheets_server._paths import PathTraversalError, resolve_under_root
from sheets_server._responses import AddContentTextResponse

_CELL_PATTERN = re.compile(r"^[A-Za-z]+[1-9][0-9]*$")


@register("sheets_server")
def add_content_text(
    file_path: Annotated[
        str,
        Field(
            description="Absolute path to the .xlsx file starting with '/' (e.g., '/data/report.xlsx')"
        ),
    ],
    tab_index: Annotated[
        int,
        Field(
            description="0-based worksheet tab index (e.g., 0 for first tab, 1 for second tab)",
            ge=0,
        ),
    ],
    cell: Annotated[
        str,
        Field(
            description="Excel cell reference (e.g., 'A1', 'B5'). Case-insensitive. The cell must be empty; this tool will not overwrite existing values"
        ),
    ],
    value: Annotated[
        str,
        Field(
            description="Value to set in the cell. Can be string, number, boolean, or formula starting with '='"
        ),
    ],
) -> str:
    """Add text, number, or formula to a specific cell in a worksheet.

    The target cell must be empty; this tool will not overwrite existing values.
    """

    if not isinstance(file_path, str) or not file_path:
        return "File path is required"
    if not file_path.startswith("/"):
        return "File path must start with /"
    if not file_path.lower().endswith(".xlsx"):
        return "File path must end with .xlsx"

    if not isinstance(tab_index, int) or tab_index < 0:
        return "Tab index must be a non-negative integer"

    if not isinstance(cell, str) or not cell:
        return "Cell reference is required"

    cell = cell.strip().upper()
    if not _CELL_PATTERN.match(cell):
        return "Cell must be a valid Excel reference like 'A1' (column letters followed by row number)"

    try:
        target_path = resolve_under_root(file_path)
    except PathTraversalError:
        return f"Invalid path: {file_path}"

    try:
        if not os.path.exists(target_path):
            return f"File not found: {file_path}"
        if not os.path.isfile(target_path):
            return f"Not a file: {file_path}"

        with open(target_path, "rb") as f:
            file_bytes = f.read()
    except Exception as exc:
        return f"Failed to read spreadsheet: {repr(exc)}"

    workbook = None
    try:
        workbook = load_workbook(BytesIO(file_bytes))

        if tab_index >= len(workbook.sheetnames):
            sheet_count = len(workbook.sheetnames)
            return f"Tab index {tab_index} is out of range. Available sheets: {sheet_count}"

        worksheet = workbook[workbook.sheetnames[tab_index]]

        try:
            current_value = worksheet[cell].value
            if current_value is not None:
                return f"Cell {cell} already contains a value: {repr(current_value)}. Cannot overwrite."
        except Exception as exc:
            return f"Invalid cell reference '{cell}': {repr(exc)}"

        worksheet[cell] = value

        workbook.save(target_path)

        response = AddContentTextResponse(
            status="success",
            cell=cell,
            tab_index=tab_index,
            file_path=file_path,
        )

    except Exception as exc:
        return f"Failed to add content: {repr(exc)}"
    finally:
        if workbook is not None:
            workbook.close()

    recalculate_formulas(target_path)

    return str(response)
