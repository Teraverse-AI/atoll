# native_tools

[简体中文](./README.md) | **English**

Atoll's replacement for Archipelago's `mcp_servers/` — the only structural change in
the repo layout. The directory mirrors Archipelago's **11 server modules** one-to-one
(plus Atoll's own `core/` plumbing): each module is a subfolder carrying its importable
server package, its `tests/`, and a README. Implemented modules port the upstream
server's **model-visible surface verbatim** (tool names, parameter schemas,
description text, result and error formats), so an LLM cannot tell Atoll's native
functions from Archipelago's MCP servers.

Tool names observed by the model follow FastMCP's gateway rule, exactly like
Archipelago: one server mounted → `{tool_name}`; several mounted →
`{server_name}_{tool_name}` (e.g. `sheets_server_read_tab`). Mount a subset with
`load_tools(["filesystem_server", "sheets_server"])` or per task via `servers = [...]`
in `task.toml`; `load_default_tools()` mounts every implemented server.

## Highlight: the LibreOffice office layer

Archipelago ships LibreOffice (`libreoffice-calc`/`libreoffice-core` in its environment
image, a staged copy for its grader) and calls `soffice` from two places: sheets_server's
`read_tab` recalculates formula cells with it, and the grader recalculates workbooks
before judging. Atoll installs the same components in the `atoll-runner` image and keeps
both upstream call sites **verbatim** — so `read_tab` recalculates formula cells exactly
as upstream does.

On top of that, `core/atoll_tools/office.py` is the one place where Atoll itself drives
LibreOffice, shared by the opt-in tools below and by the grader:

- **Forced full recalculation.** LibreOffice keeps an `.xlsx` file's cached values on
  load by default; workbooks written by libraries such as xlsxwriter cache 0 for every
  formula and set `fullCalcOnLoad` (so that Excel recalculates on open), and a plain
  conversion returns those zeros. Every call uses a throwaway profile with
  `OOXMLRecalcMode`/`ODFRecalcMode` set to "always" — the values a user sees on opening
  the file in Excel.
- **Failure is an exception** (`OfficeError`), never a silent fallback to cached values;
  a fresh profile per call, with a timeout.
- `needs_recalculation(path)`: some formula lacks a cached value, or the file asks to be
  recalculated on open.

### Opt-in office tools (off by default)

| Server | Tool | Description |
| --- | --- | --- |
| `sheets_server` | `recalculate_spreadsheet` | Fully recalculate an `.xlsx` with LibreOffice and save the computed values into the file (as opening and saving it in a spreadsheet application would); lists formula cells whose result is an error. |
| `docs_server` | `convert_document_to_pdf` | Convert a `.docx` to PDF with LibreOffice, e.g. to check pagination and layout as printed. |

The tools live in their server's package (later `presentations/` likewise) but are
registered with `@register_optin`, apart from the ported tool lists: **the default
mounted surface stays identical to Archipelago's** and the `published_schemas.json`
parity tests are unaffected. The switch resolves like `max_steps`:
`atoll-run --office-tools on|off` > task.toml `[atoll] office_tools` >
`$ATOLL_OFFICE_TOOLS` > off. Turned on, the model sees tools Archipelago does not have,
so **that run's score is no longer directly comparable with Archipelago's**.

## Module index

✅ = fully ported, tests included · 📄 = README placeholder (not ported yet)

### ✅ `filesystem/` → package `filesystem_server`

| Tool | Description |
| --- | --- |
| `list_files` | List files and folders in a path; each entry shows name and type (file/folder). |
| `read_text_file` | Read the contents of a text file; `.eml` is returned decoded (headers, bodies, attachment manifest). |
| `read_image_file` | Read an image file and return it for vision APIs. |
| `search_files` | Search for files matching a glob pattern in the given directory. |
| `get_file_metadata` | Return metadata for a file or directory (path, type, size, MIME type, permissions, modified time). |
| `get_directory_tree` | Display a directory tree structure with ASCII tree visualization. |

### ✅ `spreadsheets/` → package `sheets_server`

| Tool | Description |
| --- | --- |
| `create_spreadsheet` | Create a new `.xlsx` workbook with validated sheet definitions, headers, and rows. |
| `delete_spreadsheet` | Delete an Excel workbook file from the filesystem. |
| `read_tab` | Read worksheet data from a tab or range with optional formula extraction and truncation. |
| `read_csv` | Read a CSV file with configurable delimiter/encoding and return structured rows. |
| `list_tabs_in_spreadsheet` | List all worksheet tabs in a workbook with their metadata. |
| `add_tab` | Add a new worksheet tab to an existing workbook, with optional seed data. |
| `delete_tab` | Remove a worksheet tab from a workbook. |
| `edit_spreadsheet` | Apply a batch of spreadsheet edit operations atomically (16 operation types: cells, rows/columns, formatting, conditional formats, data validation, named ranges, images…). |
| `add_content_text` | Add text, number, or formula to a specific cell. |
| `delete_content_cell` | Clear the content of a specific cell. |
| `create_chart` | Create a chart (bar/line/pie) in a worksheet from a data range. |
| `filter_tab` | Filter worksheet rows using column conditions and optionally persist auto-filter criteria. |

### ✅ `pdfs/` → package `pdf_server`

| Tool | Description |
| --- | --- |
| `read_pdf_pages` | Read PDF pages: text, image annotation keys (`page{N}_img{M}`), strikethrough annotations, form fields; password support. |
| `search_pdf` | Search for text occurrences across all pages of a PDF document. |
| `create_pdf` | Generate a PDF document from structured blocks and optional metadata. |
| `read_image` | Retrieve an embedded image from a PDF by its annotation key. |
| `read_page_as_image` | Render a single PDF page as a JPEG image at 144 DPI. |

### ✅ `code/` → package `code_execution_server`

| Tool | Description |
| --- | --- |
| `python_code_execution` | Run Python 3 code and return stdout, stderr and exit code; the description names the sandbox's preinstalled packages (same profile as Archipelago). |
| `code_exec` | Execute a shell command in a sandboxed environment, with upstream's output heuristics (binary/HTML suppression, head/tail truncation). |

Both tools **always** run in a network-less, memory-capped Docker container
(`atoll-sandbox:latest`, built by `scripts/build_sandbox_image.sh`) — never on the host.

### ✅ `documents/` → package `docs_server`

| Tool | Description |
| --- | --- |
| `create_document` | Create a new `.docx` file from structured content blocks (paragraphs, headings, lists, tables, page/section breaks) with optional metadata. |
| `delete_document` | Delete a `.docx` document from the filesystem. |
| `get_document_overview` | Document structure overview: heading hierarchy, paragraph count, pagination info. |
| `read_document_content` | Read structured content with stable edit identifiers (`body.p.0`, `body.tbl.0.r.0.c.0`); content-sized pagination; tracked changes and embedded images surfaced. |
| `read_image` | Retrieve an embedded image by its annotation key (cached by read_document_content). |
| `add_content_text` | Insert text at an identifier (run/paragraph/cell; start or end). |
| `edit_content_text` | Replace text content at an identifier. |
| `delete_content_text` | Clear content at an identifier, or remove the element entirely. |
| `add_image` | Insert an image file into a paragraph/run, with optional sizing. |
| `modify_image` | Rotate/flip/brightness/contrast an embedded image by index. |
| `apply_formatting` | Bold/italic/underline/strikethrough/font size/color on a run, paragraph, or cell. |
| `header_footer` | Read, set, clear, or link section headers/footers (action-based). |
| `page_margins` | Read or set section page margins (action-based). |
| `page_orientation` | Read or set portrait/landscape orientation (action-based). |
| `comments` | Read, add, or delete document comments (action-based; python-docx ≥ 1.2). |

### 📄 Not ported yet (README placeholders)

| Module | Original server function | Upstream tools (summary) | Finance relevance |
| --- | --- | --- | --- |
| `edgar_sec/` | SEC EDGAR filings access & analysis | `lookup_cik`, `get_company_submissions`, `get_company_facts`, `get_company_concept`, `get_frames`, `list_filing_documents`, `get_filing_document`, `get_debt_schedule`, `get_equity_compensation`, `extract_html_table` | **High** (needs network/API) |
| `fmp/` | Market & fundamentals data (Financial Modeling Prep API) | quotes, charts, technical indicators, company info/search, financial statements, earnings/dividends, analyst data, DCF, SEC filings, news, economics, indexes, ETF/funds, forex, crypto, commodities, congressional trading | **High** (needs paid API) |
| `mail/` | Simulated mailbox app | `list_mails`, `read_mail`, `search_mail`, `send_mail`, `reply_mail`, `reply_all_mail`, `forward_mail` | Medium |
| `calendar/` | Simulated calendar app | `list_events`, `read_event`, `create_event`, `update_event`, `delete_event` | Low |
| `chat/` | Simulated team chat (channels/threads) | list/read channels & threads, users, post/reply, reactions | Low |
| `presentations/` | Slide-deck app | create/read/edit decks, slides, shapes, images, tables, charts | Low |

Each placeholder folder's README explains the original server and why it is deferred.
A task that mounts only ported servers is tool-surface-identical to the same task on
Archipelago with those MCP servers configured.

## Core plumbing (`core/` → package `atoll_tools`)

- `registry.py` — `@register("<server>")`; `load_tools([...])` applies the gateway naming rule.
  `@register_optin("<server>")` registers an opt-in office tool, added to its server only by `load_tools(..., office_tools=True)`.
- `office.py` — Atoll's LibreOffice layer (see the highlight above): `recalculate`, `convert`, `needs_recalculation`, `find_soffice` (`$ATOLL_SOFFICE` overrides the path).
- `tool_schema.py` — serves each ported tool's **vendored published schema** verbatim (see `published.py`); introspection (`Annotated[T, Field(description=...)]` or Google-style `Args:`) is the fallback for new tools.
- `published.py` — each ported server carries a `published_schemas.json`: the exact `tools/list` JSON Archipelago's gateway served at the pinned upstream commit (captured with each server's locked fastmcp version — upstream post-processes schemas with `flatten_schema`, `(Optional)`/nullable annotations, and, for documents, union merging; several tools publish a single nested `input` object). Also ports upstream's EnvelopeCompatMiddleware: flat arguments to an `input`-enveloped tool are wrapped before the call.
- `context.py` — the per-run workspace root; all paths resolve inside it, traversal out is rejected.
- `content.py` — `ImageContent`, the MCP image-block equivalent for tools that return images.

## Contract for new tools

```python
from typing import Annotated
from pydantic import Field
from atoll_tools.registry import register

@register("docs_server")
def my_tool(
    amount: Annotated[float, Field(description="What this number means.")],
) -> dict:
    """One-line summary the model sees."""
    return {"ok": True}
```

Return a `str` for verbatim model-visible text (what ported tools do), a
JSON-serializable `dict`/pydantic model for structured results, or an `ImageContent`
for images. Raised exceptions reach the model as
`Error executing tool <name>: <message>` — the MCP gateway's format.
