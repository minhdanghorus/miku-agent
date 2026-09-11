## Context

Four facts were established by reading the actual systems before any of this was designed.

**1. waku's complexity is a consequence of waku's loop, not of MCP.** `waku/tools/mcp_client.py` is
95 lines, and its own docstring says why: *"Waku's loop is synchronous; the MCP SDK is async."* It
runs an event loop on a daemon thread, holds every session on that loop through one `AsyncExitStack`
because anyio requires the stack be entered and exited on the same task, and calls tools via
`run_coroutine_threadsafe`. Miku needs none of it. `open_session` is already an
`asynccontextmanager` wrapping `open_store` and `open_checkpointer`; the tools node already awaits
`tool.ainvoke`. Porting the bridge would import a solution to a problem this repo does not have,
which is the opposite of the legibility constraint.

**2. waku has no tests for MCP.** Grepping waku's `evals/` for "mcp" returns nothing. Its wiring is
twelve lines in `waku/tools/__init__.py` inside a `try/except ImportError`, and nothing asserts any
of it. So waku is a reference for *shape*, not for *coverage* — and the two claims worth the most
here are the two it never made.

**3. `chrome-devtools-mcp` offers 29 tools.** Counted directly from a live session with the server
loaded: click, close_page, drag, emulate, evaluate_script, fill, fill_form, get_console_message,
get_network_request, handle_dialog, hover, lighthouse_audit, list_console_messages,
list_network_requests, list_pages, navigate_page, new_page, performance_analyze_insight,
performance_start_trace, performance_stop_trace, press_key, resize_page, select_page,
take_heapsnapshot, take_screenshot, take_snapshot, type_text, upload_file, wait_for.

Miku has four. Connecting this one server without filtering takes the bound tool count from 4 to
33 — and CLAUDE.md's claim that gemma is the right default for `main` is explicitly *measured*, at
four tools, on scheduling cases. Nothing in this repo has ever bound 33 tools to gemma.

**4. The servers in `D:\my-mcp` cannot be spawned by waku's spec.** `employees/employee_server.py`
imports relatively (`from . import services`), depends on `fastmcp`, and lives under its own
`.venv`. waku's spec has `command`, `args`, `env` and no working directory, so the process would
start in miku's directory against miku's interpreter and fail at import. A `cwd` field is not a
refinement; without it the stated motivation for this change does not work.

Two related observations from the same read. That server exposes `@mcp.resource` endpoints
alongside its tools, which this change will not reach. And it logs with `file=sys.stderr` — which
is correct, because under stdio the protocol owns stdout, and a server that prints to stdout
corrupts the stream. That is the first thing anyone writing an MCP server gets wrong, and it is
worth stating where someone will read it.

Three existing constraints frame the work. A gateway moves data and never reads a source.
`inspect.py` is read-only and environment-free, both pinned by tests. `config.py` is the only reader
of the environment.

## Goals / Non-Goals

**Goals:**

- Connect stdio MCP servers and bind their tools, driven entirely by configuration.
- Off by default, twice over, so nothing changes for anyone who does not opt in.
- Control which tools are bound, because one real server offers 29.
- A borrowed tool is visibly borrowed, in both gateways.
- A broken server costs a warning, never a session.
- Zero changes to `miku/graph/`.

**Non-Goals:**

- HTTP transport, OAuth, MCP resources, MCP prompts, image results.
- Truncating tool output or trimming history. See Decision 7.
- Reconnection, supervision, or health checks beyond reporting state.
- Measuring routing degradation. The allowlist is a precaution, and this change says so.

## Decisions

### 1. `langchain-mcp-adapters`, not the raw `mcp` SDK

**Chosen:** an optional extra, `uv sync --extra mcp`, pulling `langchain-mcp-adapters`.

**Rejected: write the client against the `mcp` SDK directly**, as waku does. It is about 80 lines
and it would make the protocol handshake visible, which has some value in a repo whose purpose is
partly to be legible. But the work those 80 lines do is translating an MCP tool's JSON Schema into
something LangChain can bind — and CLAUDE.md draws its line precisely there: *"Do not hand-roll
wrappers over streaming, tool binding, or retry. LangChain provides them. The provider adapter
abstracts configuration, never wire formats."* An MCP tool schema is a wire format. Writing that
translation by hand would be the same mistake the provider adapter exists to avoid, made in a new
place.

The honest cost: the MCP initialize handshake will not be visible in this repo. Accepted knowingly.
What remains hand-written is everything that is actually a decision — the config schema, the two
gates, the lifecycle, the filtering, the naming, the degradation, the inspection surface — and none
of that is wire format.

**Measured, and the assumption held.** See Spike Results below: `MultiServerMCPClient.get_tools()`
is the stateless path and spawns a process per tool call — 0.754s against 0.005s on the same tool
through a held session. The persistent path Decision 3 assumes exists and is
`client.session(name)` plus `load_mcp_tools(session, ...)`. Measured on `langchain-mcp-adapters`
0.3.2 with `mcp` 1.30.0.

### 2. `.miku/mcp.json` is the file; `Settings` holds only its path and a flag

**Chosen:** `Settings` gains `mcp_enabled: bool = False` and `mcp_config: Path`, defaulting to
`.miku/mcp.json`. `miku/mcp/config.py` reads and validates the file into `MCPServerSpec` objects.
`mcp.example.json` is committed at the repo root as a template.

**Rejected: express servers as `MIKU_`-prefixed environment variables.** A server spec is a nested
structure of variable arity — command, argument list, environment map, working directory, tool
allowlist. Flattening that into environment variables produces names like
`MIKU_MCP_SERVER_0_ARGS_2` and nothing readable.

**Rejected: `mcp.json` at the repo root.** It holds credentials for the servers it starts, and the
root is where an accidental forced `git add` finds it. `.miku/` is already gitignored, already holds
`state.db` and `traces/`, and this file is as much runtime state as configuration — it decides which
processes the agent spawns. The template at the root keeps the feature discoverable without keeping
the secrets there.

This does not weaken `config.py`'s rule, which says *nothing else reads the environment*. Reading a
file is not reading the environment. `config.py` still resolves where to look and whether to look;
it simply does not hold what is found.

### 3. Eager connection, inside the existing `AsyncExitStack`

**Chosen:** connect during `open_session`, in the same `async with` that holds the store and the
checkpointer. Tools are appended to `deps.tools` before `build_graph` binds them.

**Rejected: lazy connection on first use.** Not merely more complex — largely impossible in the
right order. `build_graph` calls `bind_tools`, so the model must be told a tool exists before it can
ask for it. A lazy client would need a cached tool manifest on disk, which is a second source of
truth about what a server offers, which goes stale silently.

The cost is startup latency, roughly 0.5-3 seconds per stdio server, paid on every `open_session`.
Decision 4 is what keeps the eval suite from paying it.

Placing the connection in the existing stack rather than a new lifecycle is what makes the
subprocess-cleanup claim (Decision 8) assertable at all: the servers die when the stack unwinds,
for the same reason the SQLite handles close.

### 4. Two gates, combined with AND

**Chosen:** MCP connects only when `MIKU_MCP_ENABLED` is true **and** the config file exists.
`mcp_enabled` defaults to false.

**Rejected: presence of the file alone**, which is waku's rule (`if mcp_config.exists()`). It has
no off switch short of moving the file. A config that is kept but not currently wanted has no way
to say so, and the failure mode is silent: a forgotten `mcp.json` spawns processes on every session
of a repo someone came back to after three months.

**Rejected: the flag alone.** Then enabling MCP without a file is a configuration error at startup,
and this feature is not important enough to fail a startup over.

The default being false is also what keeps the eval suite clean. `evals/` opens many sessions per
process; without this it would spawn many subprocesses per run. The MCP cases enable it explicitly
through `load_settings(mcp_enabled=True, mcp_config=...)`, which is the same injection pattern the
suite already uses for the stub model and the frozen clock.

### 5. Filtering at two grains: a server switch and a tool allowlist

**Chosen:** each server spec may carry `enabled: false`, and may carry a `tools` list naming which
of its tools to bind. Omitting `tools` binds all of them.

**Rejected: bind everything a server offers**, which is what waku does. Defensible for waku, whose
documented example is a two-tool demo server. Indefensible here, where the first server to be
connected offers 29 tools against a model whose suitability was measured at 4.

**Rejected: a server switch alone.** Coarser and cheaper, and adequate if only one server is ever
enabled at a time. It is rejected because it does not help the actual case: the problem is not that
`chrome-devtools-mcp` is on, it is that `chrome-devtools-mcp` is 29 tools when three would do.

**Rejected: a denylist instead of an allowlist.** A denylist means a server's next release can add
tools to Miku's prompt without anyone deciding to. An allowlist is the direction that fails safe.

A named tool that the server does not offer is a warning, not an error — the same degradation rule
the rest of this applies. Silence would be worse: a typo in an allowlist would otherwise present as
a tool the model mysteriously never calls.

### 6. Tools are namespaced by their server

**Chosen:** waku's scheme, unchanged — the server name, an underscore, the tool name.
`demo_word_count`, `chrome_navigate_page`.

**Rejected: bind the server's tool names as-is.** Two servers offering `search` collide, and the
loser is decided by list order.

**Rejected: a double-underscore form.** It is what some hosts use and it is unambiguous, but it is
longer in every prompt for no gain here, and the single-underscore form matches the sibling repo. A
name collision between a namespaced MCP tool and a native one is detected and reported at startup
rather than resolved by precedence.

### 7. Large tool output is not truncated

**Chosen:** an MCP tool's text result goes into the `ToolMessage` verbatim.

**Rejected: cap output at a configurable character count.** Five lines of code, and it would
directly address a real cost: `take_snapshot` returns a page's whole accessibility tree, tens of
kilobytes, into `state["messages"]`, which `nodes.py:187` re-sends in full on every subsequent model
call with no prompt caching. The cost of that conversation then grows quadratically.

It is rejected on the same grounds Phase 3d rejected message trimming: what the agent retains
within a conversation is *behaviour*, and deciding it inside an infrastructure change is how a
memory policy gets chosen by accident. Phase 3d made that cost visible and left it; this change
does the same.

The consequence must be said plainly rather than left for someone to find. The allowlist is the
only control, and it is manual — and the tension is real, because for `chrome-devtools-mcp` the
tool that costs the most is also the one without which the model is blind. A browser conversation
should be its own thread, and the cockpit already has a button to remove it.

### 8. Four claims, and two of them are the ones waku never made

**Chosen:** `evals/fixtures/mcp_echo_server.py`, a pure-Python `FastMCP` stdio server in the repo,
spawned with `sys.executable`. No npx, no network, no credentials. It doubles as what
`mcp.example.json` points at, so the documented example and the test fixture are one file that
cannot drift from each other. The idea is borrowed directly from waku's
`examples/mcp_demo_server.py`, which is the part of waku worth taking.

| Claim | Why it matters |
|---|---|
| A live server's tools appear in `deps.tools` under the namespaced name | the feature works |
| A server that cannot start does not prevent `open_session` returning | **waku never asserted this**; it is the whole degradation rule |
| A failing MCP tool becomes a `ToolMessage`, not an exception | the existing loop rule, extended to borrowed tools |
| Closing the session leaves no surviving subprocess | **waku never asserted this**; on Windows an orphaned child does not die with its parent, and this is the failure that would be discovered as a machine slowly filling with browsers |

**Rejected: an in-memory transport.** Faster and with no process to clean up — and for that exact
reason it cannot make the fourth claim, which is the one most likely to be false.

These cases are gated by the `mcp` extra, which the existing requirement *"Cases needing an optional
capability degrade to skipped"* already covers. No new skip policy is introduced.

### 9. stdio only, with a transport field in the spec from the first commit

**Chosen:** `MCPServerSpec.transport` exists and defaults to `"stdio"`. Only `"stdio"` is
implemented; anything else is a configuration error reported at load.

**Rejected: implement Streamable HTTP too.** The transport itself is nearly free under
`langchain-mcp-adapters` — a URL instead of a command. Authentication is not. Real remote servers
want OAuth, which wants a callback listener, in a repo whose web gateway has no authentication and
binds loopback deliberately. There is also no remote server to connect: the three servers that
motivated this change are all local processes.

**Rejected: omit the field until it is needed.** One word in a dataclass, and its absence is what
makes a later transport a migration of every config file instead of an addition.

### 10. A borrowed tool is labelled, and server state is reportable

**Chosen:** `ToolView` gains a `source` — `"native"` or the server it came from. `inspect.py` gains
`MCPServerView` and `mcp_view`, reporting each configured server, whether it is enabled, whether it
connected, how many tools it contributed, and the error if it did not. `miku mcp` and the cockpit's
tools tab both render it.

**Rejected: show MCP tools as ordinary tools.** A native tool is in the repository; a borrowed one
depends on a process that may not start tomorrow. When a tool silently disappears, the difference
between those two is the entire diagnosis, and a tools tab that cannot express it sends the user to
read code.

This follows the existing rule for what belongs in `inspect.py`: the reading has two plausible
consumers, and both are built here. That is the third time that rule has paid out, after the memory
tab in Phase 3b and the conversation listing in Phase 3d.

`mcp_view` must report *without connecting*. waku's dashboard reached the same conclusion — its
comment says *"no MCP subprocess is spawned just to render the page"* — for a stronger reason here:
`inspect.py` is pinned read-only and safe to call at any moment during a turn, and spawning a
browser to answer "which servers are configured" would break that outright. Configured state comes
from the file; live state comes from the session that already holds it.

### 11. The graph does not change

Recorded as a decision because it is the check on everything above. MCP makes `deps.tools` longer.
`nodes.py` looks a tool up by name and awaits it; it neither knows nor can tell where the tool came
from. The `tools` box in the cockpit's diagram counts tool calls, so a turn using MCP moves that
number and lights nothing new — which is correct, because nothing new is running.

If any part of this change had required editing `miku/graph/`, the seam would have been in the
wrong place, and that would have been the finding rather than the feature.

## Risks / Trade-offs

| Risk | Standing |
|---|---|
| Routing quality degrades with tool count | **Unmeasured.** The allowlist is a precaution taken on general grounds, not on a measurement. The natural sequel is a spike that finds where gemma starts misrouting. |
| A large tool result inflates a conversation permanently | **Known and accepted**, Decision 7. Controlled manually by the allowlist. |
| Credentials in `mcp.json` are not redacted from traces | **Accepted.** The sink collects secrets from the environment; a JSON file is out of its reach. `.miku/` is gitignored and trace files already carry tool arguments by a decision taken in Phase 3b. Closing it later means letting `Tracer` take secret *values*, not only variable names. |
| `mcp.json` spawns arbitrary processes | Inherent to stdio MCP and true of every MCP host. Mitigated by: off by default, a gitignored local file, and no way to configure a server through the web gateway. |
| A server dies mid-session | Its tools return a result saying it is unavailable. No reconnection. Restarting Miku is the remedy. |
| The eval suite's session count meets subprocess spawning | Prevented by the default-false flag, not by convention. |
| `langchain-mcp-adapters` session semantics | **Unverified**, Decision 1. To be spiked before implementation, because stateless mode would make stdio far more expensive than this design assumes. |

## Migration Plan

There is nothing to migrate. No stored state changes, no schema changes, no existing configuration
becomes invalid, and an installation that never creates `.miku/mcp.json` or sets
`MIKU_MCP_ENABLED` behaves exactly as it does today. The dependency arrives through an extra that
is not installed by default.

## Open Questions

- ~~Does `langchain-mcp-adapters` hold one session per server for the life of the client, or open
  one per tool call?~~ **Answered by the spike: both, and which one you get depends on which call
  you make.** See Spike Results, finding 1.
- ~~Do the servers in `D:\my-mcp` expose their useful logic as `@mcp.tool`, or mainly as
  `@mcp.resource`?~~ **Answered: tools, 13 of them against 2 resources.** See finding 4.
- How many bound tools does gemma tolerate before routing suffers? Out of scope here; the reason
  the allowlist exists. Still unmeasured, and now the only unmeasured claim this change rests on.

## Spike Results

Run before any code was written, on `langchain-mcp-adapters` 0.3.2 and `mcp` 1.30.0, against the
real servers rather than against a description of them. Five findings, two of which change the
implementation rather than confirming it.

**1. Both session modes exist, and the default is the wrong one.** `get_tools()` passes
`session=None` and a connection; the tool it returns opens a fresh session inside every call, and
the library's own docstring says so — *"A new session will be created for each tool call"*. Passing
a live session to `load_mcp_tools` instead binds the tools to that session. Measured on the Python
fixture:

| Path | First tool call | Second tool call |
|---|---|---|
| held session (`client.session(...)` + `load_mcp_tools(session, ...)`) | 0.108s | **0.005s** |
| stateless (`client.get_tools()`) | 0.754s | 0.754s |

The stateless number is a process spawn, paid again on every call. For `chrome-devtools-mcp` that
would be a browser launched and discarded per click. Decision 3's lifecycle is therefore not a
preference: it is the only one of the two that is affordable, and the client must hold the session
for the life of the session, not call `get_tools()`.

**2. Startup is at the top of the estimated range, not the middle.** The design claimed 0.5-3
seconds per stdio server. Measured: the in-repo Python fixture 0.724s to spawn and initialise, and
`npx -y chrome-devtools-mcp@latest` **2.517s**. `list_tools` is free either way (0.005s and
0.008s). So the cost is spawning, it is paid once per `open_session` per server, and a person who
enables the browser server is adding two and a half seconds to every start. The default-false gate
is what keeps that off the eval suite, and it is now a measured reason rather than a tidy one.

**3. 29 tools, confirmed against the server itself.** The design's count came from a loaded client
session; a live `list_tools` returns the same 29 names. The number the allowlist exists for is
right.

**4. `D:\my-mcp` is one server, and it is tools, not resources.** Only `employees/employee_server.py`
constructs a `FastMCP`; `accounts/` is a library with no server in it, so there are two servers to
connect on this machine and not three. That server carries 13 `@mcp.tool` against 2
`@mcp.resource`, so the resource surface this change does not reach costs it almost nothing — the
Open Question resolved the favourable way. It also confirms Context fact 4 concretely: the module
imports relatively (`from . import services`) and guards on `__main__`, so it must be started as
`-m employees.employee_server` from `D:\my-mcp` against that project's own interpreter. Without
`cwd` there is no way to express that.

**5. Two things the library already does, and one thing it does that must be undone.**

- `tool_name_prefix=True` produces exactly `<server>_<tool>` — `chrome_navigate_page`,
  `spike_echo`. Decision 6's scheme is the library's own, so task 2.4 is a flag plus a collision
  check, not a renaming pass.
- `handle_tool_errors=True` is the default, and a tool that raises inside the server comes back as
  `Error executing tool boom: deliberate failure` in the result rather than as an exception.
  Claim 3 is therefore partly the library's behaviour; the case still asserts it, because a default
  can change and this one is load-bearing.
- **A tool result is a list of LangChain content blocks, not a string.** `echo("hello")` returns
  `[{'type': 'text', 'text': 'hello', 'id': 'lc_0e7c1529-...'}]`. `nodes.py:242` is
  `content = str(output)`, so without intervention the model would be handed the Python repr of
  that list, random `lc_` uuid included. Decision 11 forbids touching `nodes.py`, so the flattening
  belongs in `miku/mcp/client.py` — which means **task 2.7 is not polish but the thing that makes
  contributed tools legible at all**, and its "preserve any text" half is the main path rather than
  the edge case.


## Deviations found while building

Recorded rather than edited into the decisions above, so that what was predicted and what
turned out to be true stay distinguishable.

**1. `Settings.mcp_config` follows `state_dir`; the literal default is only the literal
default.** Task 1.1 asked for a `Path` defaulting to `.miku/mcp.json`, and pinning it there
would have meant an eval that redirects `state_dir` to a temporary directory still reading the
developer's real server list -- and spawning whatever it named. A validator moves it with
`state_dir` unless it was set explicitly. The declared default still reads as written.

**2. `load_mcp_config` returns a config object, not "an empty list when a gate is closed".**
Task 1.3's wording and the `cli-gateway` spec pull in opposite directions: the CLI has to
report "the connector is disabled" *and still list the configured servers*, which cannot be
done from an empty list. So the file is parsed whenever it is present, and only
`connectable()` respects the gates. The gates are unchanged; what changed is which function
enforces them.

**3. The connection is nested inside `open_session`, not opened alongside the store.**
Task 3.1 said "beside `open_store` and `open_checkpointer`". Collision detection needs the
complete native tool list, and the delegating proposal tools cannot exist before `Deps` does,
so the names are not known at that point. Nesting is also the correct unwind order -- the
child processes die before the database handles. Still one lifecycle and no new lifecycle
object, which was the actual constraint.

**4. Namespacing is done here rather than with the adapter's `tool_name_prefix=True`.** The
spike found the library produces exactly `<server>_<tool>`, and finding 5 called task 2.4 "a
flag plus a collision check". It is not: the allowlist has to match on the server's own tool
names, and recovering those from a prefixed string is guesswork the moment a server is called
`my_server`. The tool is being rebuilt anyway for the content-block flattening, so the name is
constructed there, in the open.

**5. `miku mcp` starts the servers, and that is the point of it.** The `cli-gateway` spec says
the command must not "read the configuration file or contact a server itself". It opens the
connector handle -- exactly as `miku threads` opens a checkpointer handle, whose docstring
already says that opening a handle is not reading a source -- and the *reading* is
`inspect.mcp_view`. `inspect.py` itself still starts nothing, which is the rule that carries
the weight. Without this, "the tools each contributed" has no answer, and task 8.4 could not
be verified at all.

The cost, stated rather than hidden: `miku mcp` checks collisions against `build_tools` alone,
so the delegating `propose_slots` is not in that list. A server contriving to collide with
that one name would be reported when a real session opens and not by this command.

**6. `npx` works unprefixed on Windows.** The spike used `npx.cmd`, and a Windows caveat about
it nearly went into the documentation. Checked directly instead: a spec of
`{"command": "npx", "args": ["-y", "chrome-devtools-mcp@latest"]}` starts and binds its three
allowlisted tools. No caveat, and the configuration people already have works unedited.

**7. Claim 3 is partly the library's behaviour, and the case asserts it anyway.**
`handle_tool_errors` defaults to true, so a tool raising inside a server returns error text
rather than an exception before any of this repo's code is reached. The case stays, because a
library default can change and this one is load-bearing.

**8. Task 3.5 is a frozen hash manifest.** "Byte-identical" needed a mechanism. The sha256 of
each file under `miku/graph/` was recorded before the first line of this change was written
and is asserted in `test_mcp.py`, alongside a second case that greps the package for the
string `mcp` and finds nothing. Both passed at the end without any file there being touched.

## What the implementation confirmed

The three servers that motivated this change all connect, verified by running them rather than
by reasoning about them (tasks 8.4 and 8.5):

    echo             stdio    3 tools
    chrome           stdio    3 tools       <- 3 of 29, via the allowlist
    employees        stdio    13 tools      <- started through `cwd`, in its own .venv

The `chrome` line is the entire argument for Decision 5 in one row, and the `employees` line is
what proves the `cwd` field earns its place: without it that server cannot start at all.
