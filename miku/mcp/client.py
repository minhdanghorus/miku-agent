"""Connecting to external tool servers, and what that is allowed to cost.

**One session per server, held for the life of the miku session.** That is the
whole lifecycle, and it is a measured choice rather than a stylistic one.
`MultiServerMCPClient.get_tools()` is the obvious call and it is the wrong one:
it binds each tool to a connection rather than to a session, and opens a fresh
one inside every call. Measured against the in-repo fixture, on
langchain-mcp-adapters 0.3.2:

    held session     first call 0.108s   second call 0.005s
    get_tools()      every call 0.754s   (a process spawn, each time)

Under stdio that difference is a subprocess. For `chrome-devtools-mcp` it would
be a browser launched and discarded per click. So the session is entered into
the caller's `AsyncExitStack` and kept, which is also what makes the claim in
`test_mcp.py` assertable at all: the child processes die when the stack unwinds,
for the same reason the SQLite handles close.

Nothing here raises at a caller. A server that will not start costs a warning
and its own tools; it does not cost the session, the other servers, or the four
tools that live in this repository. Errors degrade, they do not crash.

What this module refuses to do: read the environment (that is `runtime/config.py`),
reach resources or prompts (only the tool surface of the protocol is covered),
and know anything about the graph. It returns a list of tools. `deps.tools` gets
longer and nothing else changes.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field

from langchain_core.tools import BaseTool, StructuredTool

from miku.mcp.config import MCPConfig, MCPServerSpec, load_mcp_config, report
from miku.runtime.config import Settings

# Where a borrowed tool records its origin, so a tool can be asked rather than
# looked up. `inspect.py` reads this and nothing else to tell the two apart.
MCP_SERVER_KEY = "mcp_server"


@dataclass(frozen=True)
class ServerState:
    """How one configured server fared. Reported; never acted on.

    `connected` is False both for a server that failed and for one that was
    never contacted, which is why `enabled` and `error` are here too: those
    three together are what lets a gateway say "disabled", "nothing configured"
    and "broken" as three different sentences.
    """

    name: str
    enabled: bool
    transport: str
    connected: bool
    tool_count: int = 0
    error: str | None = None


@dataclass(frozen=True)
class MCPConnection:
    """What a session holds: the borrowed tools, and the story of how they got here."""

    tools: list[BaseTool] = field(default_factory=list)
    states: list[ServerState] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return any(state.connected for state in self.states)


@asynccontextmanager
async def open_mcp(
    settings: Settings,
    native_names: Iterable[str] = (),
    stream=None,
) -> AsyncIterator[MCPConnection]:
    """Connect every enabled server, and close them all on exit.

    `native_names` is the built-in tool list, passed in rather than imported, so
    that a collision between a borrowed name and a built-in one is reported
    instead of resolved silently by whichever list happened to be extended last.

    Yields an empty connection -- not an error -- when either gate is closed,
    when no server is configured, or when the optional extra is not installed.
    """
    config = load_mcp_config(settings)
    problems = list(config.problems)
    report(problems, stream=stream)

    specs = config.connectable()
    if not specs:
        yield MCPConnection(states=_idle_states(config), problems=problems)
        return

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from langchain_mcp_adapters.tools import load_mcp_tools
    except ImportError as error:
        problem = f"configured but the 'mcp' extra is not installed ({error}); no server contacted"
        report([problem], stream=stream)
        problems.append(problem)
        yield MCPConnection(states=_idle_states(config), problems=problems)
        return

    client = MultiServerMCPClient({spec.name: _connection(spec) for spec in specs})
    taken = set(native_names)
    tools: list[BaseTool] = []
    states: list[ServerState] = []

    async with AsyncExitStack() as stack:
        for spec in specs:
            try:
                session = await stack.enter_async_context(client.session(spec.name))
                offered = await load_mcp_tools(session, server_name=spec.name)
            except Exception as error:  # noqa: BLE001 - degrade, do not crash
                reason = f"{type(error).__name__}: {error}"
                _warn(f"server '{spec.name}' did not start ({reason}); its tools are unavailable",
                      stream)
                states.append(
                    ServerState(spec.name, spec.enabled, spec.transport, False, error=reason)
                )
                continue

            chosen, missing = _select(offered, spec)
            for name in missing:
                _warn(f"server '{spec.name}' offers no tool named '{name}'", stream)
                problems.append(f"server '{spec.name}' offers no tool named '{name}'")

            bound = 0
            for tool in chosen:
                borrowed = _borrow(tool, spec.name)
                if borrowed.name in taken:
                    _warn(
                        f"'{borrowed.name}' from server '{spec.name}' has the same name as a tool "
                        f"already registered; it is not bound",
                        stream,
                    )
                    problems.append(f"name collision on '{borrowed.name}' from '{spec.name}'")
                    continue
                taken.add(borrowed.name)
                tools.append(borrowed)
                bound += 1

            states.append(ServerState(spec.name, spec.enabled, spec.transport, True, bound))

        states.extend(_idle_states(config, skip={state.name for state in states}))
        yield MCPConnection(tools=tools, states=states, problems=problems)


def _connection(spec: MCPServerSpec) -> dict:
    """One server spec as the adapter's connection dict. stdio only; a spec
    naming anything else never reaches here, having been refused at load."""
    connection: dict = {
        "transport": "stdio",
        "command": spec.command,
        "args": list(spec.args),
    }
    if spec.env:
        connection["env"] = dict(spec.env)
    if spec.cwd:
        connection["cwd"] = spec.cwd
    return connection


def _select(offered: Sequence[BaseTool], spec: MCPServerSpec) -> tuple[list[BaseTool], list[str]]:
    """Apply the allowlist, and say which named tools the server does not have.

    No allowlist means every tool. A name that does not match is a warning
    rather than an error, for the same reason everything else here is: a typo in
    an allowlist would otherwise present as a tool the model mysteriously never
    calls.
    """
    if spec.tools is None:
        return list(offered), []
    available = {tool.name: tool for tool in offered}
    chosen = [available[name] for name in spec.tools if name in available]
    missing = [name for name in spec.tools if name not in available]
    return chosen, missing


def _borrow(tool: BaseTool, server: str) -> BaseTool:
    """Re-present one server tool as a tool this loop can use.

    Two things happen here, and both have to, which is why the tool is rebuilt
    rather than annotated.

    *The name is namespaced.* `<server>_<tool>`, so two servers offering
    `search` do not collide and so a trace line says where a call went. The
    adapter offers the same scheme behind `tool_name_prefix=True`; it is done
    here instead because the allowlist has to match on the server's own names,
    and recovering those from a prefixed string is guesswork the moment a server
    is called `my_server`.

    *The result is flattened to text.* An MCP result arrives as a list of
    LangChain content blocks -- `[{'type': 'text', 'text': 'hi', 'id': 'lc_...'}]`
    -- and `nodes.py` does `str(output)`. Without this the model would be handed
    the repr of that list, random uuid included. Non-text blocks are replaced by
    a placeholder saying what was dropped, because accepting an image would need
    a declared vision capability, and capabilities here are declared, never
    inferred.

    A transport or session failure becomes text too. A server that died
    mid-session is a tool result the model can read and work around, not an
    exception that ends a turn.
    """
    name = f"{server}_{tool.name}"

    async def call(**kwargs) -> str:
        try:
            output = await tool.ainvoke(kwargs)
        except Exception as error:  # noqa: BLE001 - degrade, do not crash
            return f"Tool '{name}' is unavailable: {type(error).__name__}: {error}"
        return _as_text(output)

    return StructuredTool(
        name=name,
        description=tool.description or f"A tool contributed by the '{server}' server.",
        args_schema=tool.args_schema,
        coroutine=call,
        # The tool carries where it came from, rather than `inspect.py` keeping a
        # second table that has to be handed around and kept in step. A borrowed
        # tool is a thing that knows it is borrowed.
        metadata={MCP_SERVER_KEY: server},
    )


def _as_text(output: object) -> str:
    """One MCP result as the prose this repo's tools return."""
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        return _block_text(output)
    if isinstance(output, (list, tuple)):
        parts = [_block_text(block) for block in output]
        kept = [part for part in parts if part]
        return "\n".join(kept) if kept else "[the tool returned nothing]"
    return str(output)


def _block_text(block: object) -> str:
    if not isinstance(block, dict):
        return str(block)
    kind = block.get("type")
    if kind == "text":
        return str(block.get("text", ""))
    return f"[dropped: {kind or 'unknown'} content, which this agent cannot read]"


def _idle_states(config: MCPConfig, skip: set[str] | None = None) -> list[ServerState]:
    """Configured servers that were never contacted, reported as configured.

    A server that is disabled, or that sits behind a closed gate, is still
    something a person wrote down. Reporting it as absent would make "I turned
    it off" and "I never set it up" the same sentence.
    """
    skip = skip or set()
    return [
        ServerState(spec.name, spec.enabled, spec.transport, connected=False)
        for spec in config.servers
        if spec.name not in skip
    ]


def _warn(message: str, stream=None) -> None:
    print(f"[miku] mcp: {message}", file=stream or sys.stderr)
