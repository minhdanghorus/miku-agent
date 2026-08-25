"""Thread state — short-term memory, one row per conversation.

This is the LangGraph checkpointer: message history for a single thread_id,
resumable across process restarts. It is NOT where long-term facts live; see
store.py for that. Keeping the two apart is deliberate — conflating them is the
single easiest way to make an agent's memory illegible.

Side benefit worth knowing: thread_id is exactly the key a conversation-list UI
needs later, so the sidebar in a future phase needs no new data model.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from miku.runtime.config import Settings


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncSqliteSaver]:
    """Open the checkpointer over the state database for the life of a session."""
    settings.ensure_dirs()
    async with AsyncSqliteSaver.from_conn_string(str(settings.db_path)) as saver:
        yield saver
