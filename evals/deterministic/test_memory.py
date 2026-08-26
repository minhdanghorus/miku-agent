"""Memory: the two tiers, and the line between them. No model calls."""

from __future__ import annotations

import pytest

from miku.memory.checkpointer import open_checkpointer
from miku.memory.store import (
    expire_fact,
    facts_namespace,
    live_facts,
    merge_facts,
    open_store,
    recall_facts,
    remember_fact,
    supersede_fact,
)
from miku.runtime.config import load_settings


@pytest.fixture
def settings(tmp_path):
    return load_settings(state_dir=tmp_path / "state", user_id="tester")


async def test_new_thread_starts_empty(settings):
    async with open_checkpointer(settings) as saver:
        config = {"configurable": {"thread_id": "fresh"}}
        assert await saver.aget_tuple(config) is None


async def test_facts_are_recalled_after_writing(settings):
    async with open_store(settings) as store:
        await remember_fact(store, settings, "Alex prefers morning meetings")
        assert await recall_facts(store, settings) == ["Alex prefers morning meetings"]


async def test_facts_survive_reopening_the_database(settings):
    async with open_store(settings) as store:
        await remember_fact(store, settings, "Raj plays tennis on Saturdays")

    async with open_store(settings) as store:
        assert "Raj plays tennis on Saturdays" in await recall_facts(store, settings)


async def test_a_fact_written_on_one_thread_is_visible_from_another(settings):
    """Facts are cross-thread by construction: the store has no thread dimension."""
    async with open_store(settings) as store:
        await remember_fact(store, settings, "I dislike meetings before 9am")
        # The store is not keyed by thread at all, so any thread reads the same
        # namespace. This asserts that property rather than simulating threads.
        assert facts_namespace(settings) == ("tester", "facts")
        assert await recall_facts(store, settings) == ["I dislike meetings before 9am"]


async def test_users_do_not_see_each_other_facts(tmp_path):
    mine = load_settings(state_dir=tmp_path / "state", user_id="me")
    yours = load_settings(state_dir=tmp_path / "state", user_id="you")

    async with open_store(mine) as store:
        await remember_fact(store, mine, "my fact")
        assert await recall_facts(store, yours) == []


async def test_stored_facts_are_never_rewritten(settings):
    """A later, contradicting fact is appended — the earlier one stays intact."""
    async with open_store(settings) as store:
        first = await remember_fact(store, settings, "I prefer 9am")
        second = await remember_fact(store, settings, "Actually I prefer 10am")

        assert first != second
        facts = await recall_facts(store, settings)
        assert facts == ["I prefer 9am", "Actually I prefer 10am"]

        item = await store.aget(facts_namespace(settings), first)
        assert item.value["fact"] == "I prefer 9am"


async def test_recall_needs_no_model(settings):
    """Recall must not depend on credentials — it is a plain read."""
    async with open_store(settings) as store:
        await remember_fact(store, settings, "no api key was harmed")
        assert await recall_facts(store, settings) == ["no api key was harmed"]


async def test_empty_fact_is_rejected(settings):
    async with open_store(settings) as store:
        with pytest.raises(ValueError):
            await remember_fact(store, settings, "   ")


# --- supersession: the tombstone shape -------------------------------------


async def test_a_superseded_fact_keeps_its_text_and_leaves_recall(settings):
    """The two halves of the promise, asserted together.

    Recall must stop returning the fact, and the row must still hold the exact
    bytes that were written. Either one alone would be the wrong guarantee.
    """
    async with open_store(settings) as store:
        old = await remember_fact(store, settings, "I prefer meetings in the morning")
        new = await remember_fact(store, settings, "I prefer meetings in the afternoon")

        assert await supersede_fact(store, settings, old, new) is True

        assert await recall_facts(store, settings) == ["I prefer meetings in the afternoon"]
        row = (await store.aget(facts_namespace(settings), old)).value
        assert row["fact"] == "I prefer meetings in the morning"
        assert row["superseded_by"] == new
        assert row["superseded_at"]


async def test_a_row_with_no_marker_is_live(settings):
    """Rows written before consolidation existed. This is the whole migration."""
    async with open_store(settings) as store:
        await store.aput(
            facts_namespace(settings),
            "ancient",
            {"fact": "written before any of this", "created_at": "2026-01-01T00:00:00+00:00"},
        )
        assert await recall_facts(store, settings) == ["written before any of this"]


async def test_an_expired_fact_names_no_successor(settings):
    async with open_store(settings) as store:
        key = await remember_fact(store, settings, "this week I am in Hanoi")
        assert await expire_fact(store, settings, key) is True

        assert await recall_facts(store, settings) == []
        row = (await store.aget(facts_namespace(settings), key)).value
        assert row["fact"] == "this week I am in Hanoi"
        assert row["superseded_at"]
        assert "superseded_by" not in row


async def test_resolving_twice_is_refused(settings):
    """Idempotence at the storage layer, so the pass above it gets it for free."""
    async with open_store(settings) as store:
        old = await remember_fact(store, settings, "old")
        new = await remember_fact(store, settings, "new")

        assert await supersede_fact(store, settings, old, new) is True
        assert await supersede_fact(store, settings, old, new) is False


async def test_resolving_a_vanished_fact_is_refused_not_raised(settings):
    """A live session may have moved under the pass between read and write."""
    async with open_store(settings) as store:
        assert await expire_fact(store, settings, "no-such-key") is False


async def test_a_merge_links_provenance_in_both_directions(settings):
    async with open_store(settings) as store:
        a = await remember_fact(store, settings, "I like mornings")
        b = await remember_fact(store, settings, "nothing before 9am")
        c = await remember_fact(store, settings, "standup is 9:30")

        merged = await merge_facts(
            store, settings, "Mornings from 9:30, after standup; nothing before 9am", [a, b, c]
        )

        assert await recall_facts(store, settings) == [
            "Mornings from 9:30, after standup; nothing before 9am"
        ]

        forward = (await store.aget(facts_namespace(settings), merged)).value
        assert forward["derived_from"] == [a, b, c]
        for source in (a, b, c):
            row = (await store.aget(facts_namespace(settings), source)).value
            assert row["superseded_by"] == merged


async def test_a_merge_never_rewrites_a_source(settings):
    async with open_store(settings) as store:
        a = await remember_fact(store, settings, "I like mornings")
        b = await remember_fact(store, settings, "nothing before 9am")
        await merge_facts(store, settings, "Mornings, but not before 9am", [a, b])

        assert (await store.aget(facts_namespace(settings), a)).value["fact"] == "I like mornings"
        assert (await store.aget(facts_namespace(settings), b)).value["fact"] == (
            "nothing before 9am"
        )


async def test_a_merge_needs_two_sources_and_non_empty_text(settings):
    async with open_store(settings) as store:
        a = await remember_fact(store, settings, "only one")
        with pytest.raises(ValueError):
            await merge_facts(store, settings, "merged", [a])
        with pytest.raises(ValueError):
            await merge_facts(store, settings, "   ", [a, a])


async def test_nothing_is_ever_deleted(settings):
    """Row count may only grow, whatever the pass decides."""
    async with open_store(settings) as store:
        a = await remember_fact(store, settings, "one")
        b = await remember_fact(store, settings, "two")
        before = len(await store.asearch(facts_namespace(settings), limit=100))

        await merge_facts(store, settings, "one and two", [a, b])
        after = len(await store.asearch(facts_namespace(settings), limit=100))

        assert after >= before


# --- live_facts: what the pass sees ----------------------------------------


async def test_live_facts_carry_keys_and_timestamps(settings):
    async with open_store(settings) as store:
        key = await remember_fact(store, settings, "carries its key")
        [fact] = await live_facts(store, settings)

        assert fact.key == key
        assert fact.fact == "carries its key"
        assert fact.created_at


async def test_live_facts_excludes_resolved_rows(settings):
    """The pass must never see a fact the agent has already stopped seeing."""
    async with open_store(settings) as store:
        old = await remember_fact(store, settings, "superseded")
        new = await remember_fact(store, settings, "current")
        await supersede_fact(store, settings, old, new)

        assert [fact.fact for fact in await live_facts(store, settings)] == ["current"]


async def test_live_facts_is_ordered_oldest_first(settings):
    async with open_store(settings) as store:
        for text in ("first", "second", "third"):
            await remember_fact(store, settings, text)

        assert [fact.fact for fact in await live_facts(store, settings)] == [
            "first",
            "second",
            "third",
        ]
