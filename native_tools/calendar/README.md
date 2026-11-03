# calendar (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/calendar`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/calendar).

**What the original MCP server does:** a simulated calendar application — the agent
reads and manages events in the task world's calendar store.

**Upstream tools:** `list_events`, `read_event`, `create_event`, `update_event`,
`delete_event`.

**Finance relevance:** low — occasionally useful for deadline/scheduling flavor in
tasks.

**Why deferred:** stateful app simulation with its own data store.
