# filesystem

Native port of Archipelago's [`mcp_servers/filesystem`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/filesystem) (`filesystem_server`). Model-visible surface is verbatim upstream.

| Tool | Does |
| --- | --- |
| `list_files` | List files and folders in a path; each entry shows name and type. |
| `read_text_file` | Read a text file (supported extensions; `.eml` comes back decoded: headers, bodies, attachment manifest). |
| `read_image_file` | Read an image file and return it for vision APIs. |
| `search_files` | Search for files matching a glob pattern in a directory. |
| `get_file_metadata` | Metadata for a file/directory (type, size, MIME, permissions, mtime). |
| `get_directory_tree` | ASCII directory tree. |

Package: `filesystem_server/` · Tests: `tests/`. Paths resolve inside the per-run
workspace; traversal out is rejected. Divergences from upstream are noted in each
module docstring.
