## 0. Spike before building

- [x] 0.1 Install `langchain-mcp-adapters` in a scratch environment and determine whether
      `get_tools()` yields tools bound to a persistent session or opens a session per call.
      Decision 1 assumes persistent; stateless would mean a process spawn per tool call under
      stdio, which changes the lifecycle design rather than a detail of it.
- [x] 0.2 Measure stdio startup for one real server (`npx -y chrome-devtools-mcp@latest`) and for
      the in-repo Python fixture. The design claims 0.5-3 seconds; record the actual numbers, since
      this is what every `open_session` pays when the connector is enabled.
- [x] 0.3 Confirm the tool count of `chrome-devtools-mcp` against a live `list_tools`. The design
      records 29, counted from a loaded session rather than from the server itself.
- [x] 0.4 Check whether `D:\my-mcp`'s servers expose their logic as tools or mainly as resources.
      If mainly resources, say so in `design.md` under Open Questions and note that the remedy is
      in that repository.
- [x] 0.5 Write the findings into `design.md`, replacing each assumption with a measurement or
      marking it as still unverified. Prefer measured claims over expected ones.

## 1. Configuration

- [x] 1.1 `Settings.mcp_enabled: bool = False` and `Settings.mcp_config: Path` defaulting to
      `.miku/mcp.json`. Nothing else about MCP enters `Settings`.
- [x] 1.2 `miku/mcp/config.py`: `MCPServerSpec` with name, command, args, env, cwd, transport
      (default stdio), enabled (default true), tools (optional allowlist).
- [x] 1.3 `load_mcp_config(settings)` — both gates as AND; returns an empty list when either is
      closed, never raising for absence.
- [x] 1.4 Validation: duplicate server names, empty command, unimplemented transport, unreadable or
      malformed file. Each reported naming the file and the problem, none of them fatal to a
      session.
- [x] 1.5 `mcp.example.json` at the repo root, pointing at the in-repo fixture server from task 4.
- [x] 1.6 `.env.example`: `MIKU_MCP_ENABLED=false`.
- [x] 1.7 Case: both gates open activates; each gate alone does not; enabled with no file is not an
      error.
- [x] 1.8 Case: a malformed file is reported and the session still opens with built-in tools only.
- [x] 1.9 Case: an unimplemented transport is reported as a configuration error naming the server.
- [x] 1.10 Case: `config.py` still reads the environment and `miku/mcp/config.py` still does not —
      the existing environment-free pin extended to the new module.

## 2. The connector

- [x] 2.1 `pyproject.toml`: optional extra `mcp = ["langchain-mcp-adapters>=0.1"]`. Not in the
      default install.
- [x] 2.2 `miku/mcp/client.py`: connect each enabled server, per the lifecycle the 0.1 spike
      settled. Record the spike's answer in the module docstring, not only in `design.md`.
- [x] 2.3 Apply the allowlist; warn for a named tool the server does not offer.
- [x] 2.4 Namespace each tool by its server; detect and report a collision with a built-in name
      rather than resolving it by precedence.
- [x] 2.5 Degrade: a server that fails to start or initialise is skipped with a warning naming it
      and the reason; other servers and all built-in tools survive.
- [x] 2.6 A call to an unavailable server returns a tool result saying so, never raises.
- [x] 2.7 Discard non-text content, substituting a placeholder that says what was dropped; preserve
      any text in the same result.
- [x] 2.8 Case: a reachable server's tools appear in `deps.tools` under their namespaced names.
      **(Claim 1)**
- [x] 2.9 Case: a server configured with an unrunnable command does not prevent `open_session`
      returning, and the built-in tools are registered. **(Claim 2 — waku never asserted this.)**
- [x] 2.10 Case: one broken server among several does not cost the others their tools.
- [x] 2.11 Case: a failing contributed tool becomes a `ToolMessage` and the turn still produces a
      reply. **(Claim 3)**
- [x] 2.12 Case: an allowlist binds exactly its subset; no allowlist binds everything.
- [x] 2.13 Case: a disabled server is contacted not at all and still reported as configured.
- [x] 2.14 Case: two servers offering the same tool name both register, under distinct names.

## 3. Session lifecycle

- [x] 3.1 Connect inside `open_session`'s existing `AsyncExitStack`, beside `open_store` and
      `open_checkpointer`. No new lifecycle object.
- [x] 3.2 Extend `deps.tools` before `build_graph` binds them — order matters, and the delegating
      proposal tools are already appended in this window.
- [x] 3.3 Hold what `inspect.py` needs to report live server state on the session, following the
      accessor pattern Phase 3d established rather than reaching through `deps`.
- [x] 3.4 Case: closing a session leaves no surviving child process. **(Claim 4 — waku never
      asserted this, and it is the claim most likely to be false on Windows.)**
- [x] 3.5 Case: `miku/graph/` is byte-identical to its state before this change. The design says
      the graph does not change; this is what makes that a claim rather than an intention.

## 4. The test fixture

- [x] 4.1 `evals/fixtures/mcp_echo_server.py` — a pure-Python `FastMCP` stdio server, two or three
      tools, one of which fails deliberately so task 2.11 has something to call.
- [x] 4.2 Spawn it with `sys.executable`, so no npx, no network, and no credentials are involved.
- [x] 4.3 Point `mcp.example.json` at it, so the documented example and the fixture are one file
      and cannot drift apart.
- [x] 4.4 Log to stderr only, and say in the docstring why: under stdio the protocol owns stdout,
      and this is the first mistake anyone writing an MCP server makes.
- [x] 4.5 Confirm every MCP case skips with a reason naming the extra when it is not installed,
      under the existing optional-capability requirement. No new skip policy.

## 5. Inspection

- [x] 5.1 `ToolView.source` — built-in, or the server a tool came from.
- [x] 5.2 `MCPServerView`: name, enabled, transport, connected, tool count, error.
- [x] 5.3 `mcp_view(settings, servers=None)` — configured state from the file, live state from a
      session that already holds it; connection state is unknown, not an error, when no session is
      passed.
- [x] 5.4 Case: `mcp_view` starts no process, including when asked about a server that has never
      connected.
- [x] 5.5 Case: the existing read-only and environment-free pins still hold with the new functions
      present.
- [x] 5.6 Case: `tools_view` reports a built-in tool and a contributed tool with distinguishable
      sources.

## 6. Gateways

- [x] 6.1 `miku mcp` — servers, their state, and the tools each contributed; plain ASCII.
- [x] 6.2 `miku mcp` distinguishes "nothing configured" from "connector disabled" from "a server
      failed", and exits successfully in all three.
- [x] 6.3 A read endpoint serving server state through `inspect.mcp_view`.
- [x] 6.4 The cockpit's tools tab groups by origin and lists servers that contributed nothing, with
      their reason.
- [x] 6.5 Case: both gateways obtain this through `inspect.py`; neither reads the configuration
      file nor contacts a server.
- [x] 6.6 Case: the endpoint runs in-process with no port bound, as the existing web cases do.
- [x] 6.7 Confirm the peer-gateway import edge is still absent — the existing case should cover it,
      but this change adds a surface to both and is the kind of change that breaks it.

## 7. Documentation

- [x] 7.1 `CLAUDE.md` architecture map: `miku/mcp/config.py` and `miku/mcp/client.py`, each with
      the one sentence that says what it is for and what it refuses to do.
- [x] 7.2 `CLAUDE.md` commands: `uv sync --extra mcp`, `uv run miku mcp`, and how to start from
      `mcp.example.json`.
- [x] 7.3 `CLAUDE.md` known limits, seven entries, each deliberate: output is not truncated and
      why; secrets in `mcp.json` are not redacted; stdio only; tools only, not resources or
      prompts; non-text results discarded; routing quality at higher tool counts unmeasured; no
      reconnection after a server dies.
- [x] 7.4 Record the measured tool count of `chrome-devtools-mcp` beside the note that gemma's
      suitability was measured at four tools. The two numbers only mean something together.
- [x] 7.5 README: a short section on connecting a server, with the fixture as the zero-install
      example.

## 8. Closing out

- [x] 8.1 `uv run ruff check .` clean.
- [x] 8.2 `uv run pytest` green without the `mcp` extra installed, with every MCP case skipped and
      naming what is missing.
- [x] 8.3 `uv run pytest` green with the extra installed.
- [x] 8.4 Connect `chrome-devtools-mcp` with an allowlist of three tools and confirm through
      `miku mcp` that exactly three were bound. This is the motivating case; it is not done until
      it has actually run.
- [x] 8.5 Connect one server from `D:\my-mcp` using `cwd`, and confirm it starts — the field exists
      because without it this does not work, so this is the task that proves the field earns its
      place.
- [x] 8.6 Record in `design.md` anything the implementation found that the design got wrong, as a
      deviation rather than a silent edit.
