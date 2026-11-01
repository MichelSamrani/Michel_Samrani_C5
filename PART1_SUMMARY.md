# Part 1 – Reading and Exploration Summary

**Article:** Ksenia Se, *"What Is MCP, and Why Is Everyone – Suddenly! – Talking About It?"* (Hugging Face / Turing Post, March 2025)
**Server explored:** Fetch MCP Server (`modelcontextprotocol/servers/src/fetch`)

## Key MCP concepts

The article describes MCP, released by Anthropic in November 2024, as "an open standard designed to bridge AI assistants with the world of data and tools." Without a standard, every AI application needs custom code for every data source, and the article calls the result "brittle and hard to scale." MCP turns this N×M integration problem into N+M: each application implements the protocol once, and each tool or service is wrapped once as an MCP server. The article compares MCP to earlier universal standards such as USB, HTTP and ODBC.

The roles fit together like this. The **Host** is the AI application the user talks to (Claude Desktop, an IDE, or my Agents SDK agent). It owns the model and decides when outside context is needed. The host runs one **MCP Client** for each server it uses, and that client keeps a one-to-one connection with the server, exchanging JSON-RPC messages. The **MCP Server** wraps a capability such as Git, Slack or a database. It exposes **tools** that the model can call, **resources** with read-only context, and reusable **prompt templates**. The article stresses *dynamic discovery*: clients ask servers what they offer at runtime, so new tools need no hard-coded integration. It also lists open challenges: authentication and security, the cost of running many servers, and the fact that tools are only as useful as their descriptions.

## Critique of the Fetch server

Fetch exposes one tool, `fetch(url, max_length=5000, start_index=0, raw=false)`, and a `fetch` prompt for user-initiated requests. The interface is well defined. Each argument is strictly typed with bounds (for example `max_length` must be between 1 and 999999). The tool is annotated read-only and idempotent, and every argument is documented. Its behavior is predictable. Long pages end with an explicit message that names the next `start_index`, and failures (blocked by robots.txt, HTTP status of 400 or above, refused private address) return clear `isError` results instead of silent empty output.

Isolation is weaker. Behind that one tool sit retrieval, robots.txt policy, private-IP filtering, HTML-to-Markdown extraction and pagination. The `raw` flag shows the bundling most clearly, because it turns the tool into "fetch *and* convert, *or* just fetch." Following the class principles, I would split it into separate `fetch_raw` and `html_to_markdown` tools. Overall, Fetch is a good example of a clean model-facing interface, but internally it bundles several responsibilities.
