# presentations (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/presentations`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/presentations).

**What the original MCP server does:** a slide-deck application (.pptx-like): the
agent creates and edits decks — slides, shapes, images, tables, charts — and reads
them back page by page or as a whole.

**Upstream tools:** `create_slides`, `read_slides`, `read_individualslide`,
`read_completedeck`, `read_image`, `add_slide`, `delete_slides`, `edit_slides`,
`add_shape`, `add_image`, `modify_image`, `insert_chart`, `insert_table`.

**Finance relevance:** low — client-reporting decks at most.

**Why deferred:** stateful app simulation; a faithful port is a slides engine.
