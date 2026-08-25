"""Long-term facts — cross-thread memory.

The LangGraph Store supplies the storage: a namespace per user, keys we mint,
values we shape. What it does NOT supply is the interesting part of memory —
deciding *whether* something is worth remembering, and *what* to keep when facts
pile up. Both are deliberately absent in Phase 1:

  * writes happen only when the `remember` tool is called, so the agent never
    decides on its own what to persist
  * recall is one direct read, with no model call gating it
  * nothing rewrites, merges, or expires a stored fact

A retrieval gate and a consolidation pass are the first Phase 2 items. Building
them now would mean tuning them against no real data.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.runtime.config import Settings

# How many facts recall will carry into a turn. Generous for Phase 1 — with no
# gate, every fact rides along, which is fine at tens and wrong at thousands.
RECALL_LIMIT = 100


def facts_namespace(settings: Settings) -> tuple[str, ...]:
    """Where this user's facts live."""
    return (settings.user_id, "facts")


@asynccontextmanager
async def open_store(settings: Settings) -> AsyncIterator[AsyncSqliteStore]:
    """Open the fact store over the state database for the life of a session."""
    settings.ensure_dirs()
    async with AsyncSqliteStore.from_conn_string(str(settings.db_path)) as store:
        await store.setup()
        yield store


async def remember_fact(store: AsyncSqliteStore, settings: Settings, fact: str) -> str:
    """Append one fact. Returns its key.

    Every call writes a NEW key, so an earlier fact is never overwritten or
    edited — a later correction sits alongside the thing it corrects rather than
    replacing it.
    """
    text = fact.strip()
    if not text:
        raise ValueError("Cannot remember an empty fact")

    key = uuid.uuid4().hex
    await store.aput(
        facts_namespace(settings),
        key,
        {"fact": text, "created_at": datetime.now(UTC).isoformat()},
    )
    return key


async def recall_facts(
    store: AsyncSqliteStore, settings: Settings, limit: int = RECALL_LIMIT
) -> list[str]:
    """Every remembered fact for this user, oldest first.

    A direct read: no gate, no embedding lookup, no extra model call.
    """
    items = await store.asearch(facts_namespace(settings), limit=limit)
    dated = [(item.value.get("created_at", ""), item.value.get("fact", "")) for item in items]
    return [fact for _, fact in sorted(dated) if fact]
