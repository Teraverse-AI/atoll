# mail (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/mail`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/mail).

**What the original MCP server does:** a simulated mailbox application. The agent
works against a seeded mail store (inbox/folders) — reading, searching, and sending
email inside the task world, with outgoing mail captured for grading.

**Upstream tools:** `list_mails`, `read_mail`, `search_mail`, `send_mail`,
`reply_mail`, `reply_all_mail`, `forward_mail`.

**Finance relevance:** medium — realistic accounting tasks often deliver invoices
and confirmations by email.

**Why deferred:** it is a stateful app simulation with its own data store; a
faithful port is a mini mail backend, not a function wrapper. Port when email-based
tasks are added.
