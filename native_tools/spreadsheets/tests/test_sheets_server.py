"""Tests for the sheets_server tool family (port of Archipelago's sheets_server)."""

import pytest
from atoll_tools.published import wrap_flat_arguments as _wrap_flat
from openpyxl import load_workbook
from sheets_server.add_content_text import add_content_text
from sheets_server.add_tab import add_tab
from sheets_server.create_chart import create_chart
from sheets_server.create_spreadsheet import create_spreadsheet
from sheets_server.delete_content_cell import delete_content_cell
from sheets_server.delete_spreadsheet import delete_spreadsheet
from sheets_server.delete_tab import delete_tab
from sheets_server.edit_spreadsheet import edit_spreadsheet
from sheets_server.filter_tab import filter_tab
from sheets_server.list_tabs_in_spreadsheet import list_tabs_in_spreadsheet
from sheets_server.read_csv import read_csv
from sheets_server.read_tab import read_tab


def _flat(fn):
    """Call an input-enveloped tool with flat kwargs, as the provider's
    EnvelopeCompatMiddleware port allows a model to do."""

    spec = fn.__published_spec__["parameters"]["properties"]
    field_order = list(next(iter(spec.values()))["properties"])

    def call(*args, **kwargs):
        kwargs.update(zip(field_order, args, strict=False))
        return fn(**_wrap_flat(fn, kwargs))

    return call


add_tab = _flat(add_tab)
create_chart = _flat(create_chart)
create_spreadsheet = _flat(create_spreadsheet)
edit_spreadsheet = _flat(edit_spreadsheet)
filter_tab = _flat(filter_tab)
read_csv = _flat(read_csv)
read_tab = _flat(read_tab)

@pytest.fixture(autouse=True)
def no_libreoffice(monkeypatch):
    """Make LibreOffice unavailable so formula handling is deterministic and fast."""
    monkeypatch.setattr("shutil.which", lambda cmd, *args, **kwargs: None)


def _make_report(workspace) -> str:
    result = create_spreadsheet(
        directory="/",
        file_name="report.xlsx",
        sheets=[
            {
                "name": "Data",
                "headers": ["Name", "Age"],
                "rows": [["Alice", 30], ["Bob", 25]],
            }
        ],
    )
    assert result == (
        "{'status': 'success', 'file_name': 'report.xlsx', "
        "'file_path': '/report.xlsx', 'sheets_created': 1}"
    )
    return "/report.xlsx"


# ---------------------------------------------------------------- create/list/read


def test_create_list_read_roundtrip(workspace):
    path = _make_report(workspace)
    assert (workspace / "report.xlsx").exists()

    assert list_tabs_in_spreadsheet(path) == (
        "{'worksheets': [{'name': 'Data', 'index': 0, 'row_count': 3, 'column_count': 2}]}"
    )

    assert read_tab(path, 0) == (
        "Range: all\nTable:\n\tA\tB\n1\tName\tAge\n2\tAlice\t30\n3\tBob\t25"
    )


def test_create_spreadsheet_nested_directory(workspace):
    result = create_spreadsheet(
        directory="/reports/2024",
        file_name="q1.xlsx",
        sheets=[{"name": "S"}],
    )
    assert result == (
        "{'status': 'success', 'file_name': 'q1.xlsx', "
        "'file_path': '/reports/2024/q1.xlsx', 'sheets_created': 1}"
    )
    assert (workspace / "reports" / "2024" / "q1.xlsx").exists()


def test_create_spreadsheet_validation_errors(workspace):
    base = {"directory": "/", "file_name": "x.xlsx"}
    assert create_spreadsheet(directory="data", file_name="x.xlsx", sheets=[{"name": "A"}]) == (
        "Directory must start with /"
    )
    assert create_spreadsheet(**base, sheets=[]) == "Sheets must be a non-empty list"
    assert create_spreadsheet(
        directory="/", file_name="x.txt", sheets=[{"name": "A"}]
    ) == "File name must end with .xlsx"
    assert create_spreadsheet(
        directory="/", file_name="a/b.xlsx", sheets=[{"name": "A"}]
    ) == "File name cannot contain /"
    assert create_spreadsheet(
        **base, sheets=[{"name": "A"}, {"name": "A"}]
    ) == "Duplicate sheet name 'A'"
    assert create_spreadsheet(
        **base, sheets=[{"name": "A", "headers": ["h1", "h2"], "rows": [["only-one"]]}]
    ) == "Row 0 in sheet 'A' must match header length"


def test_read_tab_single_cell_and_range(workspace):
    path = _make_report(workspace)
    assert read_tab(path, 0, cell_range="A2") == "{'cell': 'A2', 'value': 'Alice'}"
    assert read_tab(path, 0, cell_range="A1:B2") == (
        "Range: A1:B2\nTable:\n\tA\tB\n1\tName\tAge\n2\tAlice\t30"
    )
    # Compact mode is csv.writer output: \r\n line separators (upstream parity)
    assert read_tab(path, 0, compact=True) == "Name,Age\r\nAlice,30\r\nBob,25"


def test_read_tab_truncation_notice(workspace):
    path = _make_report(workspace)
    assert read_tab(path, 0, max_rows=1) == (
        "Range: all\n"
        "\n⚠️  Output truncated to 1 rows, 2 cols. Sheet may contain more data.\n"
        "   To see all data, use: max_rows=None, max_cols=None\n"
        "   Note: This may produce very large output and use excessive tokens.\n\n"
        "Table:\n\tA\tB\n1\tName\tAge"
    )


def test_read_tab_errors(workspace):
    path = _make_report(workspace)
    assert read_tab("report.xlsx", 0) == "File path must start with /"
    assert read_tab("/report.txt", 0) == "File path must end with .xlsx"
    assert read_tab("/../evil.xlsx", 0) == "Invalid path: /../evil.xlsx"
    assert read_tab("/missing.xlsx", 0) == "File not found: /missing.xlsx"
    assert read_tab(path, 5) == "Tab index 5 is out of range. Available sheets: 1"


# ---------------------------------------------------------------- read_csv


def test_read_csv(workspace):
    (workspace / "data.csv").write_text("name,qty\nalpha,1\nbeta,2\n")
    assert read_csv("/data.csv") == (
        "File: /data.csv\nRows: 2, Columns: 2\nTable:\n"
        "\tA\tB\nH\tname\tqty\n2\talpha\t1\n3\tbeta\t2"
    )
    assert read_csv("/data.csv", compact=True) == "name,qty\r\nalpha,1\r\nbeta,2"
    assert read_csv("/data.csv", row_limit=1) == (
        "File: /data.csv\n"
        "\n⚠️  Output truncated to 1 rows. File may contain more data.\n"
        "   To see all data, use: row_limit=None\n"
        "   Note: This may produce very large output and use excessive tokens.\n\n"
        "Rows: 1, Columns: 2\nTable:\n\tA\tB\nH\tname\tqty\n2\talpha\t1"
    )
    assert read_csv("/data.xlsx") == "File path must end with .csv"
    assert read_csv("/nope.csv") == "File not found: /nope.csv"


# ---------------------------------------------------------------- add_tab / delete_tab


def test_add_and_delete_tab(workspace):
    path = _make_report(workspace)

    result = add_tab(
        path, "Extra", sheet_data={"headers": ["City"], "rows": [["NYC"]]}
    )
    assert result == (
        "{'status': 'success', 'tab_name': 'Extra', 'file_path': '/report.xlsx', 'rows_added': 2}"
    )

    assert add_tab(path, "Extra") == (
        "Tab 'Extra' already exists. Existing tabs: Data, Extra"
    )
    assert add_tab(path, "Bad/Name") == "Tab name cannot contain '/'"

    assert delete_tab(path, 3) == "Tab index 3 is out of range. Available sheets: 2"
    assert delete_tab(path, 1) == (
        "{'status': 'success', 'tab_name': 'Extra', 'tab_index': 1, 'file_path': '/report.xlsx'}"
    )
    # The last-remaining-tab check fires before the index check
    assert delete_tab(path, 0) == (
        "Cannot delete the only remaining tab. Excel requires at least one worksheet."
    )


# ---------------------------------------------------------------- edit_spreadsheet


def test_edit_spreadsheet_cells_rows_and_format(workspace):
    path = _make_report(workspace)
    result = edit_spreadsheet(
        path,
        [
            {"type": "append_rows", "sheet": "Data", "rows": [["Cara", 41]]},
            {"type": "set_cell", "sheet": "Data", "cell": "a5", "value": "note"},
            {
                "type": "format_cells",
                "sheet": "Data",
                "range": "A1:B1",
                "font_bold": True,
                "fill_color": "FFFF00",
            },
            {"type": "set_column_width", "sheet": "Data", "column": "A", "width": 20},
            {"type": "freeze_panes", "sheet": "Data", "cell": "A2"},
        ],
    )
    assert result == (
        "{'status': 'success', 'file_path': '/report.xlsx', 'operations_applied': 5}"
    )

    wb = load_workbook(workspace / "report.xlsx")
    ws = wb["Data"]
    assert ws["A4"].value == "Cara"
    assert ws["A5"].value == "note"  # set_cell uppercases 'a5'
    assert ws["A1"].font.bold is True
    assert ws.column_dimensions["A"].width == 20
    wb.close()


def test_edit_spreadsheet_formulas_and_read_back(workspace):
    create_spreadsheet(
        directory="/", file_name="calc.xlsx", sheets=[{"name": "S", "rows": [[1], [2]]}]
    )
    result = edit_spreadsheet(
        "/calc.xlsx",
        [{"type": "set_cell", "sheet": "S", "cell": "A3", "value": "=SUM(A1:A2)"}],
    )
    assert result == (
        "{'status': 'success', 'file_path': '/calc.xlsx', 'operations_applied': 1}"
    )

    # With include_formulas, the formula renders inline in the grid.
    assert read_tab("/calc.xlsx", 0, include_formulas=True) == (
        "Range: all\nTable:\n\tA\n1\t1\n2\t2\n3\t(=SUM(A1:A2))"
    )

    # Without include_formulas (and no LibreOffice to recalculate), the formula
    # string is shown with the uncached-values warning.
    assert read_tab("/calc.xlsx", 0) == (
        "Range: all\n"
        "\n⚠️  Sheet contains 1 formula cell(s) but cached values are not available. "
        "Showing formula strings instead of computed values.\n\n"
        "Table:\n\tA\n1\t1\n2\t2\n3\t=SUM(A1:A2)"
    )

    # Single-cell formula read
    assert read_tab("/calc.xlsx", 0, cell_range="A3", include_formulas=True) == (
        "{'cell': 'A3', 'value': None, 'formula': '=SUM(A1:A2)'}"
    )


def test_edit_spreadsheet_errors(workspace):
    path = _make_report(workspace)
    assert edit_spreadsheet(path, []) == "Operations must be a non-empty list"
    assert edit_spreadsheet(
        "/missing.xlsx", [{"type": "set_cell", "cell": "A1", "value": 1}]
    ) == "File not found: /missing.xlsx"
    assert edit_spreadsheet(
        path, [{"type": "rename_sheet", "sheet": "Nope", "new_name": "X"}]
    ) == "Sheet 'Nope' does not exist"
    # A malformed operation raises (upstream's gateway rejected it pre-call).
    with pytest.raises(Exception):
        edit_spreadsheet(path, [{"type": "not_an_operation"}])


# ---------------------------------------------------------------- add/delete content


def test_add_content_text_and_delete_content_cell(workspace):
    path = _make_report(workspace)

    assert add_content_text(path, 0, "d1", "Note") == (
        "{'status': 'success', 'cell': 'D1', 'tab_index': 0, 'file_path': '/report.xlsx'}"
    )
    assert add_content_text(path, 0, "A1", "x") == (
        "Cell A1 already contains a value: 'Name'. Cannot overwrite."
    )
    assert add_content_text(path, 0, "1A", "x") == (
        "Cell must be a valid Excel reference like 'A1' (column letters followed by row number)"
    )

    assert delete_content_cell(path, 0, "D1") == (
        "{'status': 'success', 'cell': 'D1', 'tab_index': 0, "
        "'file_path': '/report.xlsx', 'old_value': 'Note'}"
    )
    # Clearing an already-empty cell omits old_value
    assert delete_content_cell(path, 0, "D1") == (
        "{'status': 'success', 'cell': 'D1', 'tab_index': 0, 'file_path': '/report.xlsx'}"
    )


# ---------------------------------------------------------------- filter_tab


def test_filter_tab_matches_and_output_format(workspace):
    path = _make_report(workspace)
    result = filter_tab(path, 0, [{"column": "Name", "operator": "equals", "value": "alice"}])
    assert result == (
        "Range: all\nFilters applied: 1\nRows matched: 1/2\n"
        "Table:\n\tA\tB\nH\tName\tAge\n2\tAlice\t30"
    )

    # Numeric comparison via column letter
    result = filter_tab(path, 0, [{"column": "B", "operator": "greater_than", "value": 26}])
    assert result == (
        "Range: all\nFilters applied: 1\nRows matched: 1/2\n"
        "Table:\n\tA\tB\nH\tName\tAge\n2\tAlice\t30"
    )

    # The auto-filter (range and criteria) persists into the file
    wb = load_workbook(workspace / "report.xlsx")
    assert wb["Data"].auto_filter.ref == "A1:B3"
    wb.close()


def test_filter_tab_no_match_or_logic_and_errors(workspace):
    path = _make_report(workspace)
    assert filter_tab(
        path, 0, [{"column": "Name", "operator": "equals", "value": "Zed"}]
    ) == (
        "Range: all\nFilters applied: 1\nRows matched: 0/2\nTable: (no matching rows)"
    )

    # OR logic matches both rows
    result = filter_tab(
        path,
        0,
        [
            {"column": "Name", "operator": "equals", "value": "Alice"},
            {"column": "Age", "operator": "less_than", "value": 26},
        ],
        match_all=False,
    )
    assert result.startswith("Range: all\nFilters applied: 2\nRows matched: 2/2\n")

    assert filter_tab(path, 0, []) == "At least one filter condition is required"
    bad_col = filter_tab(path, 0, [{"column": "Nope", "operator": "is_empty"}])
    assert bad_col.startswith("Filter error: Column 'Nope' not found.")
    # An unknown operator fails FilterTabInput validation, as upstream's
    # FastMCP pre-call validation rejected it (never reaching the tool body).
    with pytest.raises(Exception, match="Invalid operator: wat"):
        filter_tab(path, 0, [{"column": "A", "operator": "wat"}])
    assert filter_tab(path, 0, [{"column": "A", "operator": "is_empty"}], cell_range="A1") == (
        "Cell range must be a range like 'A1:D100', not a single cell"
    )
    assert filter_tab("/x.txt", 0, [{"column": "A", "operator": "is_empty"}]) == (
        "File path must end with .xlsx"
    )


def test_filter_tab_compact_and_no_headers(workspace):
    path = _make_report(workspace)
    result = filter_tab(
        path,
        0,
        [{"column": "A", "operator": "is_not_empty"}],
        use_headers=False,
        compact=True,
    )
    assert result == "Name,Age\r\nAlice,30\r\nBob,25"


# ---------------------------------------------------------------- create_chart


def test_create_chart(workspace):
    path = _make_report(workspace)
    assert create_chart(path, "Data", "A1:B3", chart_type="bar", title="Ages") == (
        "Chart 'Ages' created in Data at position E2"
    )
    wb = load_workbook(workspace / "report.xlsx")
    assert len(wb["Data"]._charts) == 1
    wb.close()

    # Untitled chart falls back to the chart type in the message
    assert create_chart(path, "Data", "A1:B3", chart_type="pie", position="g5") == (
        "Chart 'pie' created in Data at position G5"
    )


def test_create_chart_errors(workspace):
    path = _make_report(workspace)
    assert create_chart(path, "Data", "A1") == "Data range must be a valid range like 'A1:C10'"
    assert create_chart(path, "Nope", "A1:B3") == "Sheet 'Nope' does not exist"
    assert create_chart(path, "Data", "A1:B1") == (
        "Data range 'A1:B1' has only one row but include_header=True. "
        "Either provide at least 2 rows of data or set include_header=False."
    )


# ---------------------------------------------------------------- delete_spreadsheet


def test_delete_spreadsheet(workspace):
    path = _make_report(workspace)
    assert delete_spreadsheet(path) == "{'status': 'success', 'file_path': '/report.xlsx'}"
    assert not (workspace / "report.xlsx").exists()
    # Deleting a missing file still succeeds (upstream contract)
    assert delete_spreadsheet(path) == "{'status': 'success', 'file_path': '/report.xlsx'}"
    assert delete_spreadsheet("nope.xlsx") == "File path must start with /"
    assert delete_spreadsheet("/nope.txt") == "File path must end with .xlsx"
    assert delete_spreadsheet("/../nope.xlsx") == "Invalid path: /../nope.xlsx"
