"""Atoll's LibreOffice layer (atoll_tools.office) and the opt-in office tools."""

import os

import docx
import openpyxl
import pytest
import xlsxwriter
from atoll_tools import office
from atoll_tools.context import set_workspace_root
from atoll_tools.registry import OPTIN, SERVERS, load_tools

needs_soffice = pytest.mark.skipif(office.find_soffice() is None, reason="LibreOffice not installed")


def _openpyxl_formula_book(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A1"], ws["A2"], ws["A3"] = 2, 3, "=A1*A2"
    wb.save(path)


def _xlsxwriter_formula_book(path):
    # xlsxwriter caches 0 for every formula and sets fullCalcOnLoad.
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet("S")
    ws.write("A1", 20)
    ws.write("A2", 1)
    ws.write_formula("A3", "=A1+A2")
    wb.close()


def test_needs_recalculation(tmp_path):
    plain = tmp_path / "plain.xlsx"
    wb = openpyxl.Workbook()
    wb.active["A1"] = 1
    wb.save(plain)
    uncached, placeholder = tmp_path / "uncached.xlsx", tmp_path / "placeholder.xlsx"
    _openpyxl_formula_book(uncached)
    _xlsxwriter_formula_book(placeholder)
    assert office.needs_recalculation(plain) is False
    assert office.needs_recalculation(uncached) is True
    # Cached (0) but fullCalcOnLoad: Excel would recompute on open.
    assert openpyxl.load_workbook(placeholder, data_only=True)["S"]["A3"].value == 0
    assert office.needs_recalculation(placeholder) is True


def test_missing_soffice_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ATOLL_SOFFICE", str(tmp_path / "no-such-soffice"))
    src = tmp_path / "a.xlsx"
    _openpyxl_formula_book(src)
    with pytest.raises(office.OfficeError, match="not found"):
        office.recalculate(src)


@needs_soffice
def test_recalculate_fills_missing_and_overrides_placeholder_caches(tmp_path):
    uncached, placeholder = tmp_path / "uncached.xlsx", tmp_path / "placeholder.xlsx"
    _openpyxl_formula_book(uncached)
    _xlsxwriter_formula_book(placeholder)
    out = office.recalculate(uncached, tmp_path / "o1")
    assert openpyxl.load_workbook(out, data_only=True).active["A3"].value == 6
    out = office.recalculate(placeholder, tmp_path / "o2")
    # The forced recalculation replaces xlsxwriter's placeholder 0.
    assert openpyxl.load_workbook(out, data_only=True)["S"]["A3"].value == 21
    # Formula text survives the round trip; the input is untouched.
    assert openpyxl.load_workbook(out)["S"]["A3"].value == "=A1+A2"
    assert openpyxl.load_workbook(placeholder, data_only=True)["S"]["A3"].value == 0


@needs_soffice
def test_convert_docx_to_pdf(tmp_path):
    src = tmp_path / "memo.docx"
    d = docx.Document()
    d.add_paragraph("hello")
    d.save(src)
    pdf = office.convert(src, "pdf", tmp_path / "out")
    assert pdf.suffix == ".pdf" and pdf.read_bytes().startswith(b"%PDF")


def test_optin_tools_are_off_by_default():
    default = load_tools(["sheets_server", "docs_server"])
    opted = load_tools(["sheets_server", "docs_server"], office_tools=True)
    added = sorted(set(opted) - set(default))
    assert added == ["docs_server_convert_document_to_pdf", "sheets_server_recalculate_spreadsheet"]
    # The ported surfaces themselves never contain them.
    assert "recalculate_spreadsheet" not in SERVERS["sheets_server"]
    assert "convert_document_to_pdf" not in SERVERS["docs_server"]
    assert set(OPTIN) == {"sheets_server", "docs_server"}
    # Single server mounted: unprefixed, like every other tool.
    assert "recalculate_spreadsheet" in load_tools(["sheets_server"], office_tools=True)


@needs_soffice
def test_recalculate_spreadsheet_tool(tmp_path):
    set_workspace_root(tmp_path)
    _xlsxwriter_formula_book(tmp_path / "book.xlsx")
    tool = load_tools(["sheets_server"], office_tools=True)["recalculate_spreadsheet"]
    out = tool(file_path="/book.xlsx")
    assert "Recalculated 1 formula cell(s)" in out and "No formula errors." in out
    assert openpyxl.load_workbook(tmp_path / "book.xlsx", data_only=True)["S"]["A3"].value == 21
    assert tool(file_path="book.xlsx") == "File path must start with /"
    assert tool(file_path="/missing.xlsx") == "File not found: /missing.xlsx"


@needs_soffice
def test_recalculate_spreadsheet_reports_errors(tmp_path):
    set_workspace_root(tmp_path)
    wb = openpyxl.Workbook()
    wb.active["A1"] = "=1/0"
    wb.save(tmp_path / "bad.xlsx")
    out = load_tools(["sheets_server"], office_tools=True)["recalculate_spreadsheet"](file_path="/bad.xlsx")
    assert "1 formula cell(s) evaluate to an error" in out and "Sheet!A1 = #DIV/0!" in out


@needs_soffice
def test_convert_document_to_pdf_tool(tmp_path):
    set_workspace_root(tmp_path)
    d = docx.Document()
    d.add_paragraph("hello")
    os.makedirs(tmp_path / "r")
    d.save(tmp_path / "r" / "memo.docx")
    tool = load_tools(["docs_server"], office_tools=True)["convert_document_to_pdf"]
    assert tool(file_path="/r/memo.docx") == "Converted /r/memo.docx to /r/memo.pdf."
    assert (tmp_path / "r" / "memo.pdf").read_bytes().startswith(b"%PDF")
    assert tool(file_path="/r/memo.docx", output_path="/../x.pdf").startswith("Invalid path")
