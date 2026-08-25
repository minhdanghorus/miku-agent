"""The tool registry — what the graph binds, and how a call finds its tool.

One list, built per session because the tools close over this session's database
and store. No module-level globals: two sessions in one process (which is what
the eval suite is) must not share state.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool
from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.runtime.config import Settings
from miku.tools.memory import build_memory_tools
from miku.tools.scheduling import build_scheduling_tools


class UnknownToolError(KeyError):
    """The model asked for a tool that is not registered."""


def build_tools(settings: Settings, store: AsyncSqliteStore) -> list[BaseTool]:
    """Every tool this session exposes to the model."""
    return [*build_scheduling_tools(settings), *build_memory_tools(settings, store)]


def lookup(tools: list[BaseTool], name: str) -> BaseTool:
    """Find a tool by name, or say plainly that it does not exist.

    Never falls back to a similarly-named tool: executing something the model did
    not ask for is worse than reporting the miss.
    """
    for tool in tools:
        if tool.name == name:
            return tool
    known = ", ".join(sorted(tool.name for tool in tools))
    raise UnknownToolError(f"Unknown tool {name!r}. Registered tools: {known}")
