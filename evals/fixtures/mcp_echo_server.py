"""A tiny MCP server, in this repository, for the cases to talk to.

Borrowed in shape from waku's `examples/mcp_demo_server.py`, which is the part
of waku worth taking. It is spawned with `sys.executable`, so connecting to it
needs no npx, no network, and no credentials -- which is what lets the MCP
cases assert the one claim an in-memory transport cannot: that a real child
process is gone once the session closes.

It is also what `mcp.example.json` points at. The documented example and the
test fixture are one file on purpose, so neither can drift into describing a
server that no longer behaves that way.

**Everything here logs to stderr, and nothing prints to stdout.** Under stdio
the protocol owns stdout: a stray `print` lands in the middle of a JSON-RPC
frame and the client sees a parse error rather than a message about whatever
was being logged. It is the first mistake anyone writing an MCP server makes,
and it is worth being explicit about in the one MCP server this repo owns.

Run directly (`python evals/fixtures/mcp_echo_server.py`) it serves over stdio
and waits, which looks like a hang. That is correct; it is waiting for a client.
"""

from __future__ import annotations

import os
import sys

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """Repeat the given text back, unchanged."""
    _log(f"echo({text!r})")
    return text


@mcp.tool()
def word_count(text: str) -> str:
    """Count the words in the given text."""
    _log(f"word_count({text!r})")
    return f"{len(text.split())} words"


@mcp.tool()
def explode() -> str:
    """Always fail. Exists so a case has something that breaks on purpose."""
    _log("explode()")
    raise RuntimeError("this tool fails deliberately")


def _log(message: str) -> None:
    """stderr, always. See the module docstring for why this is not `print`."""
    print(f"[echo-server] {message}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    # An optional path to write this process's id into, before serving. It is
    # how the "no surviving child process" case gets something to check: there
    # is otherwise no way to learn the pid of a process the adapter spawned, and
    # that claim is the one most likely to be false on Windows, where a child
    # does not die with its parent.
    if len(sys.argv) > 1:
        with open(sys.argv[1], "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))

    mcp.run()
