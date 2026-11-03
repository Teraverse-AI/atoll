# documents

Native port of Archipelago's [`mcp_servers/documents`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/documents) (`docs_server`), all 15 tools, model-visible surface verbatim.

| Tool | Does |
| --- | --- |
| `create_document` | New `.docx` from structured blocks (paragraph, heading, bullet/numbered list, table, page/section break) + optional metadata. |
| `delete_document` | Delete a `.docx` file. |
| `get_document_overview` | Heading hierarchy, paragraph count, pagination info. |
| `read_document_content` | Structured content with stable edit identifiers (`body.p.0`, `body.tbl.0.r.0.c.0`); content-sized pagination; tracked changes (`.ins.N`/`.del.N` read-only runs) and embedded images surfaced. |
| `read_image` | Embedded image by annotation key (cached by read_document_content). |
| `add_content_text` / `edit_content_text` / `delete_content_text` | Insert / replace / clear-or-remove text at an identifier (run, paragraph, or cell). |
| `add_image` / `modify_image` | Insert an image file; rotate/flip/brightness/contrast by image index. |
| `apply_formatting` | Bold/italic/underline/strikethrough/font size/color on runs under an identifier. |
| `header_footer` | Read/set/clear/link section headers and footers (action-based). |
| `page_margins` / `page_orientation` | Read or set margins / portrait-landscape (action-based). |
| `comments` | Read/add/delete document comments (python-docx ≥ 1.2). |

Package: `docs_server/` · Tests: `tests/`. Engine: python-docx (same as upstream);
Pillow for image extraction/manipulation.

Tool-mode note: upstream's server can also expose 2 consolidated meta-tools
(`USE_INDIVIDUAL_TOOLS` off). Atoll ports the expanded/individual mode — the surface
Mercor's APEX runs used (the APEX paper's tool-usage table counts per-operation tools
like "Read document content", impossible in meta mode) and the mode upstream's own
README and smoke test call the default.
