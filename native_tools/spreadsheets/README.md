# spreadsheets

Native port of Archipelago's [`mcp_servers/spreadsheets`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/spreadsheets) (`sheets_server`), all 12 tools, model-visible surface verbatim.

| Tool | Does |
| --- | --- |
| `create_spreadsheet` | Create a new `.xlsx` workbook with validated sheets, headers, rows. |
| `delete_spreadsheet` | Delete a workbook file. |
| `read_tab` | Read a tab or range; optional formulas; exact grid/compact rendering with truncation notices. |
| `read_csv` | Read a CSV with configurable delimiter/encoding. |
| `list_tabs_in_spreadsheet` | List tabs with metadata (incl. merged-range-aware data extent). |
| `add_tab` / `delete_tab` | Add or remove a worksheet tab. |
| `edit_spreadsheet` | Atomic batch of 16 operation types: cells, rows/columns, formatting, conditional formats, validation, named ranges, images… |
| `add_content_text` | Set one cell (text/number/formula), no silent overwrite. |
| `delete_content_cell` | Clear one cell. |
| `create_chart` | Bar/line/pie chart from a data range. |
| `filter_tab` | Filter rows by column conditions; optionally persist auto-filter criteria. |

Package: `sheets_server/` · Tests: `tests/`. LibreOffice (`soffice`) is used for formula
recalculation like upstream — it is installed in `atoll-runner`, as in Archipelago's
environment image; without it (e.g. a bare host run), formula strings are shown with the
same upstream warning.

Opt-in (off by default, not part of Archipelago's surface — see
[`../README.en.md`](../README.en.md#highlight-the-libreoffice-office-layer)):
`recalculate_spreadsheet` in `sheets_server/office_tools.py`.
