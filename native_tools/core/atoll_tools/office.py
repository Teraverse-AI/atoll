"""Atoll's LibreOffice layer: headless recalculation and conversion of office files.

Archipelago ships LibreOffice (``libreoffice-calc``/``libreoffice-core`` in its
environment image, a staged copy for its grader) and calls ``soffice`` from two
places: sheets_server's ``read_tab`` recalculation fallback and the grader's file
extraction. Atoll installs the same packages in ``atoll-runner`` and keeps both
upstream call sites verbatim; this module is the one place where Atoll itself
drives LibreOffice — for the opt-in office tools and for the grader's patches.

What it adds over a plain ``soffice --convert-to``:

- **Forced full recalculation.** LibreOffice keeps an .xlsx file's cached values on
  load by default, so a workbook written with placeholder results (xlsxwriter stores
  0 for every formula and sets ``fullCalcOnLoad`` so that Excel recalculates on open)
  would come back still showing zeros. Each call runs with a throwaway profile whose
  ``OOXMLRecalcMode``/``ODFRecalcMode`` is "always", so every formula is recomputed —
  what a user opening the file in Excel sees.
- **Failure is an exception** (:class:`OfficeError`), never a silent fallback.
- **Isolation per call**: a fresh profile directory (concurrent calls never share
  one) and a timeout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import openpyxl

__all__ = [
    "OfficeError",
    "convert",
    "find_soffice",
    "needs_recalculation",
    "recalculate",
]

DEFAULT_TIMEOUT = 300

# Registry fragment for a throwaway user profile: recalculate on load, always.
_RECALC_ALWAYS = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop></item>
<item oor:path="/org.openoffice.Office.Calc/Formula/Load"><prop oor:name="ODFRecalcMode" oor:op="fuse"><value>0</value></prop></item>
</oor:items>
"""


class OfficeError(RuntimeError):
    """LibreOffice is missing, failed, timed out, or produced no output."""


def find_soffice() -> str | None:
    """The ``soffice`` binary: ``$ATOLL_SOFFICE`` if set, else the one on PATH."""
    explicit = os.environ.get("ATOLL_SOFFICE")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    return shutil.which("soffice") or shutil.which("libreoffice")


def needs_recalculation(path: str | Path) -> bool:
    """True iff the workbook has formulas whose shown values may not be current.

    That is: some formula cell has no cached value (openpyxl and most libraries
    write none), or the file asks Excel to recalculate everything on open
    (``fullCalcOnLoad`` — xlsxwriter's placeholder zeros). A workbook without
    formulas, or one last saved by a spreadsheet application that computed every
    value, needs nothing.
    """
    path = Path(path)
    try:
        with zipfile.ZipFile(path) as z:
            full_calc = 'fullCalcOnLoad="1"' in z.read("xl/workbook.xml").decode("utf-8", "replace")
    except (KeyError, zipfile.BadZipFile, OSError):
        full_calc = False
    formulas = openpyxl.load_workbook(path, data_only=False, read_only=True)
    try:
        coords = {
            (ws.title, cell.coordinate)
            for ws in formulas.worksheets
            for row in ws.iter_rows()
            for cell in row
            if getattr(cell, "data_type", None) == "f"
        }
    finally:
        formulas.close()
    if not coords:
        return False
    if full_calc:
        return True
    values = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        return any(
            cell.value is None and (ws.title, getattr(cell, "coordinate", None)) in coords
            for ws in values.worksheets
            for row in ws.iter_rows()
            for cell in row
        )
    finally:
        values.close()


def _run(src: Path, out_dir: Path, target: str, *, calc: bool, timeout: int) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise OfficeError("LibreOffice (soffice) not found: install libreoffice-calc / "
                          "libreoffice-writer, or set $ATOLL_SOFFICE")
    work = Path(tempfile.mkdtemp(prefix="atoll-office-"))
    try:
        profile = work / "profile"
        (profile / "user").mkdir(parents=True)
        (profile / "user" / "registrymodifications.xcu").write_text(_RECALC_ALWAYS)
        staged = work / "in" / src.name
        staged.parent.mkdir()
        shutil.copy2(src, staged)
        cmd = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
               "--norestore", "--nolockcheck"]
        if calc:
            cmd.append("--calc")
        cmd += ["--convert-to", target, "--outdir", str(work / "out"), str(staged)]
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise OfficeError(f"LibreOffice timed out after {timeout}s on {src.name}") from exc
        produced = sorted((work / "out").glob(f"{staged.stem}.*")) if (work / "out").exists() else []
        if proc.returncode != 0 or not produced:
            detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[-500:]
            raise OfficeError(f"LibreOffice could not convert {src.name} to {target}"
                              f" (exit {proc.returncode}): {detail}")
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / produced[0].name
        shutil.move(str(produced[0]), dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def recalculate(src: str | Path, out_dir: str | Path | None = None, *,
                timeout: int = DEFAULT_TIMEOUT) -> Path:
    """Fully recalculate a workbook and save the result as .xlsx; returns its path.

    The input is never modified. ``out_dir`` defaults to a new temporary directory
    (the caller owns it). Raises :class:`OfficeError` on any failure.
    """
    src = Path(src)
    out = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="atoll-recalc-"))
    return _run(src, out, "xlsx", calc=True, timeout=timeout)


def convert(src: str | Path, fmt: str, out_dir: str | Path | None = None, *,
            timeout: int = DEFAULT_TIMEOUT) -> Path:
    """Convert an office file (``fmt`` e.g. ``"pdf"``, ``"docx"``, ``"xlsx"``); returns its path.

    Spreadsheets are fully recalculated on the way (same profile as
    :func:`recalculate`). The input is never modified; raises :class:`OfficeError`.
    """
    src = Path(src)
    out = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="atoll-convert-"))
    is_sheet = src.suffix.lower() in {".xlsx", ".xlsm", ".xls", ".ods", ".csv"}
    return _run(src, out, fmt, calc=is_sheet, timeout=timeout)
