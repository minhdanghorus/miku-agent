"""The `remember` tool — the only way a fact reaches long-term memory.

Phase 1 has no retrieval gate: nothing infers what is worth keeping. If the user
does not ask to be remembered, nothing is written. That is a smaller system than
waku's, on purpose — the gate is Phase 2, and it wants real accumulated facts to
tune against.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.memory.store import remember_fact
from miku.runtime.config import Settings


def build_memory_tools(settings: Settings, store: AsyncSqliteStore) -> list[BaseTool]:
    """The memory tools, bound to this session's store."""

    async def remember(fact: str) -> str:
        """Save one durable fact about the user, available in every future conversation.

        Args:
            fact: A single self-contained statement, e.g. "Alex prefers morning meetings".
        """
        await remember_fact(store, settings, fact)
        return f"Remembered: {fact.strip()}"

    return [
        StructuredTool.from_function(
            coroutine=remember,
            name="remember",
            description=(
                "Save one durable fact about the user so it is available in future "
                "conversations. Use it when the user asks to be remembered, or states a "
                "lasting preference. One fact per call."
            ),
        ),
    ]
