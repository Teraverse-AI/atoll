# chat (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/chat`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/chat).

**What the original MCP server does:** a simulated team-chat application
(Slack-like): channels, threads, user profiles, reactions — the agent reads
history and posts messages inside the task world.

**Upstream tools:** `list_channels`, `get_channel_history`, `get_thread_replies`,
`get_users`, `get_user_profile`, `post_message`, `reply_to_thread`,
`add_reaction`, `delete_post`.

**Finance relevance:** low.

**Why deferred:** stateful app simulation with its own data store.
