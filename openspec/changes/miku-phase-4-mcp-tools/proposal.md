## Why

Miku has four tools, all hand-written, all living in `miku/tools/`. Adding a fifth means writing
Python in this repo. That is the right cost for a scheduling tool the agent's identity is built
around; it is the wrong cost for a browser driver, an HR database, or a stock ledger that already
exist as MCP servers on this machine.

The Model Context Protocol is the seam that removes that cost. A server speaks a small protocol
over stdio; a client lists its tools and calls them. `D:\waku-agent` already does this, in 95 lines
(`waku/tools/mcp_client.py`), and the servers to connect are already here: `chrome-devtools-mcp`
via npx, and two hand-written FastMCP servers in `D:\my-mcp` (an employee database and a trading
account ledger).

Most of waku's 95 lines do not apply. They exist because waku's loop is synchronous, so an MCP
session has to be pinned to an event loop on a daemon thread and reached through
`run_coroutine_threadsafe`. Miku is async end to end: `open_session` is already an
`asynccontextmanager` holding an async store and an async checkpointer, and `tools` already awaits
`tool.ainvoke`. The bridge machinery is not a feature to port — it is the part to leave behind.

What is left is the part waku did not do: deciding *which* tools get bound, what happens when a
server dies, and whether a gateway can tell a borrowed tool from a native one. waku registers every
tool every server offers and has no test for any of it.

## What Changes

- **An MCP connector.** `miku/mcp/` — a config reader and a client. Connections open eagerly inside
  `open_session`'s existing `AsyncExitStack`, beside the store and the checkpointer, and close with
  it. Tools land in `deps.tools` and nothing else changes: the graph, the nodes, the tracer, the
  budget, and every event shape are untouched.
- **A configuration file, because a configuration file is what this is.** `.miku/mcp.json`, with
  `mcp.example.json` committed at the repo root as its template. `Settings` holds the path and an
  enabling flag; it does not hold server specs. `config.py` remains the only reader of the
  *environment*, which is what its rule says — reading a JSON file is not that.
- **Two gates, and they are AND.** `MIKU_MCP_ENABLED` defaults to false, and the file must exist.
  Either alone is not enough. The flag is the brake for the case the file outlives the intent to
  use it; the file's absence is what keeps the eval suite from spawning subprocesses.
- **Filtering, at two grains.** A server may be disabled without deleting its entry, and a server
  may declare which of its tools to bind. This is not tidiness. `chrome-devtools-mcp` offers 29
  tools; miku has 4. Binding all of them takes the model from choosing among 4 to choosing among
  33, and the measurement behind `main` defaulting to gemma was taken at 4.
- **Degradation, as waku does it.** A server that fails to connect is skipped with a warning and
  the session opens. A call to a server that is gone returns a tool result saying so. Errors
  degrade; they do not crash.
- **A borrowed tool is labelled as borrowed.** `ToolView` gains a source, and the inspection
  surface gains a view of server state. Both gateways show it: `miku mcp` in the terminal, a split
  between native and MCP in the cockpit's tools tab. An MCP tool can vanish between two starts, and
  "why does Miku no longer have that tool" is a question the cockpit should answer.
- **Four cases, two of which waku never proved.** That a bad server does not stop a session
  opening, and that subprocesses die when the session closes.

## Capabilities

### New Capabilities

- `mcp-tools`: how an external MCP server becomes callable tools — how servers are configured and
  gated, how tools are selected and named, when connections open and close, what happens when a
  server is absent or broken, and what parts of the protocol are deliberately not reached.

### Modified Capabilities

- `runtime-inspection`: the tool report distinguishes native tools from borrowed ones, and the
  surface can report configured MCP servers and their connection state. Still read-only, still
  environment-free, and still never spawning anything to answer a question.
- `cli-gateway`: `miku mcp` lists configured servers and the tools they contributed.
- `web-gateway`: the cockpit's tools tab groups tools by origin.

## Impact

**Code**

| File | Change |
|---|---|
| `miku/mcp/config.py` | new — parse and validate `mcp.json` into `MCPServerSpec` |
| `miku/mcp/client.py` | new — connect, filter, namespace, degrade |
| `miku/runtime/config.py` | `mcp_enabled`, `mcp_config` |
| `miku/runtime/session.py` | connect inside the existing `AsyncExitStack`; extend `deps.tools` |
| `miku/runtime/inspect.py` | `ToolView.source`, `MCPServerView`, `mcp_view` |
| `miku/gateway/cli.py` | `miku mcp` |
| `miku/gateway/web.py` | one read endpoint for server state |
| `miku/gateway/static/*` | tools tab grouped by origin |
| `mcp.example.json` | new — committed template |
| `evals/fixtures/mcp_echo_server.py` | new — pure-Python stdio MCP server |
| `evals/deterministic/test_mcp.py` | new — four claims |
| `pyproject.toml` | optional extra `mcp` |

**Not changed, deliberately:** `miku/graph/` in its entirety, `Session.run_turn`, the tracer, the
event shape, the budget, the store, the checkpointer, and every existing tool. MCP makes
`deps.tools` longer and does nothing else. If a change here required editing `nodes.py`, the seam
would be in the wrong place.

**Out of scope, and which phase owns it**

| Deferred | Why, and where it goes |
|---|---|
| Streamable HTTP transport | `transport` is a field in the spec from the first commit, defaulting to `"stdio"`, so the seam exists. Only stdio is implemented and tested. A static-token HTTP server is a small follow-up. |
| OAuth to remote servers | Needs a callback listener, and the web gateway has no authentication and binds loopback. Its own phase, if a remote server is ever wanted. |
| MCP resources and prompts | The protocol has three surfaces; this change reaches one. Noted because `D:\my-mcp`'s employee server exposes `@mcp.resource` endpoints that will therefore be invisible to Miku. |
| Non-text tool results | Images are discarded and replaced with a placeholder, as waku does. Accepting them needs a vision capability flag in `providers.py`, and capability flags are declared, never inferred. |
| Truncating large tool output | A decision about what the agent remembers within a conversation, which is behaviour. Phase 3d refused to decide message trimming inside a UI change for the same reason; this change refuses to decide it inside an infrastructure one. The allowlist is the control, and it is manual. |
| Reconnecting a server that dies mid-session | A dead server returns a tool result saying it is gone. Automatic reconnection is a supervision policy, and this change has no measurement to size one. |
| Measuring how routing quality degrades with tool count | The allowlist exists because of this risk, not because the risk was measured. "How many tools before gemma misroutes" is a spike, and it is the natural sequel to this change. |
| Redacting secrets held in `mcp.json` | The sink collects secrets from the environment; values in a JSON file are not reached. An extension of a risk Phase 3b accepted knowingly, not a new one. |
