"""Memory: the two tiers, and the line between them. No model calls."""

from __future__ import annotations

import pytest

from miku.memory.checkpointer import open_checkpointer
from miku.memory.store import facts_namespace, open_store, recall_facts, remember_fact
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
