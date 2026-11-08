"""Atoll opt-in office tool for sheets_server (not part of Archipelago's surface).

Registered with ``register_optin``: mounted only when a run turns office tools on
(``--office-tools on`` / task.toml ``[atoll] office_tools`` / ``$ATOLL_OFFICE_TOOLS``).
"""

import os
import shutil
from typing import Annotated

import openpyxl
from atoll_tools.office import OfficeError, recalculate
from atoll_tools.registry import register_optin
from pydantic import Field

from sheets_server._paths import PathTraversalError, resolve_under_root

_ERROR_LITERALS = ("#REF!", "#NAME?", "#VALUE!", "#DIV/0!", "#N/A", "#NUM!", "#NULL!", "Err:")


@register_optin("sheets_server")
def recalculate_spreadsheet(
    file_path: Annotated[
        str,
        Field(
            description="Absolute path to the .xlsx file to recalculate, starting with '/' (e.g., '/data/model.xlsx')"
        ),
    ],
) -> str:
    """Recalculate every formula in an .xlsx workbook with LibreOffice and save the computed values into the file, as opening and saving it in a spreadsheet application would; reports formula cells whose result is an error."""

    if not isinstance(file_path, str) or not file_path:
        return "File path is required"
    if not file_path.startswith("/"):
        return "File path must start with /"
    if not file_path.lower().endswith(".xlsx"):
        return "File path must end with .xlsx"
    try:
        target = resolve_under_root(file_path)
    except PathTraversalError:
        return f"Invalid path: {file_path}"
    if not os.path.isfile(target):
        return f"File not found: {file_path}"

    try:
        result = recalculate(target)
    except OfficeError as exc:
        return f"Failed to recalculate spreadsheet: {exc}"
    try:
        formulas = openpyxl.load_workbook(result, data_only=False, read_only=True)
        values = openpyxl.load_workbook(result, data_only=True, read_only=True)
        n_formulas, errors = 0, []
        try:
            for ws in formulas.worksheets:
                vs = values[ws.title]
                for row in ws.iter_rows():
                    for cell in row:
                        if getattr(cell, "data_type", None) != "f":
                            continue
                        n_formulas += 1
                        value = vs[cell.coordinate].value
                        if isinstance(value, str) and value.startswith(_ERROR_LITERALS):
                            errors.append(f"{ws.title}!{cell.coordinate} = {value}")
        finally:
            formulas.close()
            values.close()
        shutil.copyfile(result, target)
    except Exception as exc:
        return f"Failed to recalculate spreadsheet: {repr(exc)}"
    finally:
        shutil.rmtree(result.parent, ignore_errors=True)

    lines = [f"Recalculated {n_formulas} formula cell(s) in {file_path}."]
    if errors:
        lines.append(f"{len(errors)} formula cell(s) evaluate to an error:")
        lines += [f"- {e}" for e in errors[:50]]
        if len(errors) > 50:
            lines.append(f"- ... {len(errors) - 50} more")
    else:
        lines.append("No formula errors.")
    return "\n".join(lines)
