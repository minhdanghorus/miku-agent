"""Long-term facts — cross-thread memory.

The LangGraph Store supplies the storage: a namespace per user, keys we mint,
values we shape. What it does NOT supply is the interesting part of memory —
deciding *whether* something is worth remembering, and *what* to keep when facts
pile up.

Writes still happen only when the `remember` tool is called, so the agent never
decides on its own what to persist, and recall is still one direct read with no
model call gating it. What changed in Phase 2.5 is the third gap: facts no longer
only accumulate. `consolidate.py` resolves contradictions, duplicates, fragments,
and expired time-bound facts.

It does so **without ever destroying anything**. Two rules hold throughout:

  * A row is never deleted, and the `fact` text of an existing row is never
    rewritten. Resolution is recorded by stamping `superseded_at` onto the row.
  * `superseded_at` — not `superseded_by` — is what makes a row dead. An expired
    fact has no successor to point at, so the timestamp is the only marker every
    resolution shares.

A row written before any of this existed has neither field. Absent reads as live,
so the old database is already correct and no migration exists.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from langgraph.store.sqlite.aio import AsyncSqliteStore

from miku.runtime.config import Settings

# How many facts recall will carry into a turn. Generous — with no retrieval
# gate, every live fact rides along, which is fine at tens and wrong at
# thousands. Selection is the second Phase 2.5 change.
RECALL_LIMIT = 100


@dataclass(frozen=True)
class LiveFact:
    """One unresolved fact, with the two things recall throws away.

    `recall_facts` returns bare strings because that is all a prompt needs. The
    consolidation pass needs the key (to write a tombstone against) and the
    timestamp (to check that a supersession points forwards in time).
    """

    key: str
    fact: str
    created_at: str


def facts_namespace(settings: Settings) -> tuple[str, ...]:
    """Where this user's facts live."""
    return (settings.user_id, "facts")


def is_live(value: dict) -> bool:
    """Whether a stored row still counts.

    Keyed on `superseded_at` rather than `superseded_by` because an expired fact
    is resolved but has no successor. Rows predating consolidation have neither
    field and are therefore live, which is the whole migration story.
    """
    return not value.get("superseded_at")


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
    replacing it. Consolidation later decides which of the two wins, and records
    that decision beside them both.
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
    """Every live fact for this user, oldest first.

    A direct read: no gate, no embedding lookup, no extra model call. Resolved
    facts are filtered out here, which is why neither the `assemble` node nor the
    `propose_slots` tool had to change to benefit from consolidation.
    """
    items = await store.asearch(facts_namespace(settings), limit=limit)
    dated = [
        (item.value.get("created_at", ""), item.value.get("fact", ""))
        for item in items
        if is_live(item.value)
    ]
    return [fact for _, fact in sorted(dated) if fact]


async def live_facts(
    store: AsyncSqliteStore, settings: Settings, limit: int = RECALL_LIMIT
) -> list[LiveFact]:
    """Live facts with their keys and timestamps, oldest first.

    The consolidation pass's view of memory. Same filter as `recall_facts`, so
    the pass can never propose an operation against a fact the agent has already
    stopped seeing.
    """
    items = await store.asearch(facts_namespace(settings), limit=limit)
    live = [
        LiveFact(
            key=item.key,
            fact=item.value.get("fact", ""),
            created_at=item.value.get("created_at", ""),
        )
        for item in items
        if is_live(item.value)
    ]
    return sorted((fact for fact in live if fact.fact), key=lambda f: (f.created_at, f.key))


async def _resolve(
    store: AsyncSqliteStore,
    settings: Settings,
    key: str,
    successor_key: str | None,
) -> bool:
    """Stamp a row as resolved, preserving everything already on it.

    Returns False if the row is gone or was already resolved. Both are ordinary
    outcomes rather than errors: the pass reads, then thinks, then writes, and a
    live session may have moved underneath it in between.
    """
    namespace = facts_namespace(settings)
    item = await store.aget(namespace, key)
    if item is None or not is_live(item.value):
        return False

    # A copy, not a mutation in place: the value that came back belongs to the
    # store, and `fact` and `created_at` must survive this untouched.
    value = dict(item.value)
    value["superseded_at"] = datetime.now(UTC).isoformat()
    if successor_key is not None:
        value["superseded_by"] = successor_key

    await store.aput(namespace, key, value)
    return True


async def supersede_fact(
    store: AsyncSqliteStore, settings: Settings, key: str, successor_key: str
) -> bool:
    """Record that `key` has been corrected by `successor_key`."""
    return await _resolve(store, settings, key, successor_key)


async def expire_fact(store: AsyncSqliteStore, settings: Settings, key: str) -> bool:
    """Record that `key` was time-bound and its window has passed.

    Resolved like any other, but naming no successor — there is nothing that
    replaced it, which is exactly what makes this the sharpest operation in the
    set.
    """
    return await _resolve(store, settings, key, None)


async def merge_facts(
    store: AsyncSqliteStore, settings: Settings, text: str, source_keys: list[str]
) -> str:
    """Write the merged fact, then resolve each source against it. Returns its key.

    The merged fact is a NEW row rather than one of the sources rewritten, so the
    no-text-rewrite rule holds without exception and no key ever ends up holding
    text nobody wrote. `derived_from` and each source's `superseded_by` make the
    provenance walkable in both directions.
    """
    merged = text.strip()
    if not merged:
        raise ValueError("Cannot merge into an empty fact")
    if len(source_keys) < 2:
        raise ValueError("A merge needs at least two sources")

    key = uuid.uuid4().hex
    await store.aput(
        facts_namespace(settings),
        key,
        {
            "fact": merged,
            "created_at": datetime.now(UTC).isoformat(),
            "derived_from": list(source_keys),
        },
    )
    for source in source_keys:
        await _resolve(store, settings, source, key)
    return key
