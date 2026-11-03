# edgar_sec (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/edgar_sec`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/edgar_sec).

**What the original MCP server does:** access to SEC EDGAR — company filings
(10-K/10-Q etc.), XBRL company facts and concepts, CIK lookup, filing documents,
and derived analyses such as debt schedules and equity compensation extracted from
filing HTML.

**Upstream tools:** `lookup_cik`, `get_company_submissions`, `get_company_facts`,
`get_company_concept`, `get_frames`, `list_filing_documents`,
`get_filing_document`, `get_debt_schedule`, `get_equity_compensation`,
`extract_html_table`, `health_check` (plus meta variants `edgar_filings`,
`edgar_analysis`, `edgar_documents`, `edgar_lookup`, `edgar_schema`).

**Finance relevance:** HIGH — public-company filing analysis is core finance work.
First candidate for a future port.

**Why deferred:** wraps the live SEC EDGAR API — needs network access and rate
limiting, which conflicts with Atoll's offline, laptop-local task design. A port
would either proxy the API or replay a fixture snapshot per task.
