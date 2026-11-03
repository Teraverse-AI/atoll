# pdfs

Native port of Archipelago's [`mcp_servers/pdfs`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/pdfs) (`pdf_server`), model-visible surface verbatim. Engine: PyMuPDF (AGPL — see the License note in the root README) + reportlab for creation.

| Tool | Does |
| --- | --- |
| `read_pdf_pages` | Read pages: text, image annotation keys (`page{N}_img{M}`), strikethrough annotations, form fields; password support. |
| `search_pdf` | Search text across pages with page/line/char positions. |
| `create_pdf` | Generate a PDF from structured blocks with post-write validation. |
| `read_image` | Retrieve an embedded image by its annotation key. |
| `read_page_as_image` | Render one page as a JPEG (144 DPI). |

Package: `pdf_server/` · Tests: `tests/`. OCR degrades with upstream's own
"pytesseract is not installed" notes; divergences (PyMuPDF reading-order text vs
upstream's pypdf layout mode) are documented per module.
