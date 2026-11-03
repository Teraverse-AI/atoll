# fmp (not ported)

Placeholder mirroring Archipelago's [`mcp_servers/fmp`](https://github.com/Mercor-Intelligence/archipelago/tree/main/mcp_servers/fmp).

**What the original MCP server does:** market and fundamentals data via the
Financial Modeling Prep API (plus a companion auth server). Tool families cover:
quotes, charts and technical indicators, company information/search/directory,
financial statements, earnings/dividends/splits and transcripts, analyst data,
DCF valuation, SEC filings, news, economics, indexes, market hours/performance,
ETF & mutual funds, forex, crypto, commodities, and congressional trading.

**Finance relevance:** HIGH — market-data lookups complement accounting tasks.

**Why deferred:** wraps an external paid API (key + network). A port would either
proxy FMP live or replay recorded fixtures per task (upstream itself ships a
fixture/seed pipeline for determinism).
